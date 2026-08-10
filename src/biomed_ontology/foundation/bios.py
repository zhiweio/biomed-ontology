"""BIOS_v3 下载与初始化。

默认路径：全量从 Hugging Face 下载并灌入 GraphDB。
许可：CC-BY-NC-ND-4.0，必须 HMD_BIOS_LICENSE_ACK。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from biomed_ontology.config import Settings, settings
from biomed_ontology.foundation.graphdb import GraphDbClient, ensure_repository
from biomed_ontology.foundation.graphs import BIOS_NS, GRAPH_BIOMEDICAL, HMD_NS

__all__ = [
    "BIOS_HF_REPO",
    "BIOS_LICENSE",
    "BIOS_SOURCE_URL",
    "BiosConcept",
    "BiosLicenseGate",
    "ExternalIdIndex",
    "build_external_id_index",
    "download_bios_full",
    "initialize_bios",
    "load_bios_subset_jsonl",
    "read_bios_init_marker",
]

BIOS_SOURCE_URL = "https://huggingface.co/datasets/THUMedInfo/BIOS_v3"
BIOS_HF_REPO = "THUMedInfo/BIOS_v3"
BIOS_LICENSE = "CC-BY-NC-ND-4.0"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = REPO_ROOT / "data" / "cache" / "bios_v3"
DEFAULT_SUBSET = REPO_ROOT / "data" / "foundation" / "bios_subset.jsonl"
INIT_MARKER = ".initialized"


@dataclass(frozen=True)
class BiosLicenseGate:
    acknowledged: bool = False
    purpose: str = "poc"

    @classmethod
    def from_settings(cls, cfg: Settings | None = None) -> BiosLicenseGate:
        cfg = cfg or settings
        ack = (cfg.bios_license_ack or "").strip().lower()
        if not ack:
            return cls(False, "poc")
        return cls(True, ack)

    def allow_full_load(self) -> bool:
        return self.acknowledged and self.purpose in {
            "poc",
            "evaluation",
            "licensed",
        }

    def require(self) -> None:
        if not self.allow_full_load():
            raise PermissionError(
                f"BIOS_v3 许可为 {BIOS_LICENSE}（见 {BIOS_SOURCE_URL}）。"
                "请设置 HMD_BIOS_LICENSE_ACK=poc|evaluation|licensed 后重试。"
            )


@dataclass(frozen=True)
class BiosConcept:
    bios_id: str
    preferred_term: str | None
    terms: list[str]
    external_ids: list[str]
    semtypes: list[str]

    @property
    def uri_curie(self) -> str:
        return f"BIOS:{self.bios_id}"


@dataclass
class ExternalIdIndex:
    by_external: dict[str, list[str]]
    by_term: dict[str, list[str]]

    def lookup_external(self, xid: str) -> list[str]:
        return list(self.by_external.get(xid, []) or self.by_external.get(xid.lower(), []))

    def save_sqlite(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE ext (external_id TEXT NOT NULL, bios_curie TEXT NOT NULL)")
            conn.execute("CREATE TABLE term (term TEXT NOT NULL, bios_curie TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO ext VALUES (?, ?)",
                [(k, v) for k, vs in self.by_external.items() for v in vs],
            )
            conn.executemany(
                "INSERT INTO term VALUES (?, ?)",
                [(k, v) for k, vs in self.by_term.items() for v in vs],
            )
            conn.commit()
        finally:
            conn.close()


def load_bios_subset_jsonl(path: Path) -> Iterator[BiosConcept]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row: dict[str, Any] = json.loads(line)
            yield BiosConcept(
                bios_id=str(row["bios_id"]),
                preferred_term=row.get("preferred_term"),
                terms=list(row.get("terms", [])),
                external_ids=list(row.get("external_ids", [])),
                semtypes=list(row.get("semtypes", [])),
            )


def build_external_id_index(
    concepts: Iterator[BiosConcept] | list[BiosConcept],
) -> ExternalIdIndex:
    by_ext: dict[str, list[str]] = {}
    by_term: dict[str, list[str]] = {}
    for c in concepts:
        curie = c.uri_curie
        for xid in c.external_ids:
            by_ext.setdefault(xid, []).append(curie)
            by_ext.setdefault(xid.lower(), []).append(curie)
        for t in c.terms + ([c.preferred_term] if c.preferred_term else []):
            key = t.strip().lower()
            if key:
                by_term.setdefault(key, []).append(curie)
    return ExternalIdIndex(by_external=by_ext, by_term=by_term)


def download_bios_full(cache_dir: Path | None = None) -> Path:
    """从 HF 下载全量数据集到 cache_dir。"""
    dest = cache_dir or DEFAULT_CACHE
    dest.mkdir(parents=True, exist_ok=True)
    # 已有 Concepts 归档则跳过重下（勿依赖 .initialized：那只表示灌库结果）
    if list(dest.glob("Concepts*.7z")) or list(dest.glob("Concepts*.txt")):
        return dest
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "需要 huggingface_hub：请 uv sync（默认依赖已含 huggingface_hub）"
        ) from exc
    snapshot_download(
        repo_id=BIOS_HF_REPO,
        repo_type="dataset",
        local_dir=str(dest),
        local_dir_use_symlinks=False,
    )
    return dest


def _bios_max_concepts(cfg: Settings | None = None) -> int:
    """0 = 不截断（全量 ~2.2e7）。"""
    return max(0, (cfg or settings).bios_max_concepts)


def _bios_batch_size(cfg: Settings | None = None) -> int:
    return max(50, (cfg or settings).bios_batch_size)


# 全量 BIOS ~2.2e7；图中已超过此阈值则视为已灌过（防 marker 被 subset 冲掉后误重灌）
_FULL_GRAPH_MIN_CONCEPTS = 1_000_000


def read_bios_init_marker(cache_dir: Path | None = None) -> dict[str, Any] | None:
    """读取 ``data/cache/bios_v3/.initialized``；不存在或损坏则返回 None。"""
    marker = (cache_dir or DEFAULT_CACHE) / INIT_MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _marker_is_full(marker: dict[str, Any]) -> bool:
    source = str(marker.get("source") or "")
    if source == "subset" or source.startswith("subset"):
        return False
    return "full" in source or int(marker.get("concepts") or 0) >= _FULL_GRAPH_MIN_CONCEPTS


def _marker_rank(marker: dict[str, Any]) -> tuple[int, int]:
    """越大表示越“完整”，用于防止 subset 覆盖全量 marker。"""
    return (1 if _marker_is_full(marker) else 0, int(marker.get("concepts") or 0))


def _write_init_marker(
    cache: Path,
    meta: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    marker_path = cache / INIT_MARKER
    existing = read_bios_init_marker(cache)
    if existing and not force and _marker_rank(existing) > _marker_rank(meta):
        print(
            "[bios] keep stronger init marker "
            f"(existing concepts={existing.get('concepts')} source={existing.get('source')}; "
            f"not overwriting with concepts={meta.get('concepts')} source={meta.get('source')})",
            flush=True,
        )
        return
    marker_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _graph_bios_concept_count(client: GraphDbClient) -> int | None:
    """COUNT biomedical skos:Concept；失败返回 None（勿当成 0）。"""
    sparql = f"""
SELECT (COUNT(*) AS ?n) WHERE {{
  GRAPH <{GRAPH_BIOMEDICAL}> {{
    ?s a <http://www.w3.org/2004/02/skos/core#Concept> .
  }}
}}
"""
    try:
        rows = client.query(sparql)
        if not rows:
            return 0
        return int(float(rows[0].get("n") or 0))
    except Exception:
        return None


def _graph_has_bios_concepts(client: GraphDbClient) -> bool | None:
    """GraphDB biomedical 图是否已有 BIOS Concept。None=探测失败。"""
    sparql = f"""
ASK {{
  GRAPH <{GRAPH_BIOMEDICAL}> {{
    ?s a <http://www.w3.org/2004/02/skos/core#Concept> .
  }}
}}
"""
    try:
        with httpx.Client(timeout=30.0) as http:
            r = http.post(
                client.sparql_url,
                data={"query": sparql},
                headers={"Accept": "application/sparql-results+json"},
            )
            r.raise_for_status()
            return bool(r.json().get("boolean"))
    except Exception:
        return None


def _bios_load_satisfied(
    *,
    full: bool,
    marker: dict[str, Any] | None,
    max_concepts: int,
    graph_ready: bool | None,
    graph_count: int | None = None,
) -> bool:
    """marker / GraphDB 是否已满足当前初始化意图。"""
    # GraphDB 已有近全量 → 即使 marker 被 subset 冲掉也跳过，并可由调用方修复 marker
    if (
        full
        and graph_count is not None
        and graph_count >= _FULL_GRAPH_MIN_CONCEPTS
        and (max_concepts == 0 or graph_count >= max_concepts)
    ):
        return True

    if not marker:
        return False
    concepts = int(marker.get("concepts") or 0)
    if concepts < 10:
        return False
    source = str(marker.get("source") or "")
    if full:
        if source == "subset" or source.startswith("subset"):
            return False
        done_max = marker.get("max_concepts")
        try:
            done_max_i = int(done_max) if done_max is not None else 0
        except (TypeError, ValueError):
            done_max_i = 0
        # 先前截断、现在要更大/全量 → 需重灌
        if max_concepts == 0 and done_max_i > 0:
            return False
        if max_concepts > 0 and 0 < done_max_i < max_concepts:
            return False
    # graph_ready False=确认空库需重灌；None=探测失败时信 marker
    return graph_ready is not False


def _iter_concepts_tsv_lines(lines: Iterator[str], *, max_concepts: int) -> Iterator[BiosConcept]:
    """解析 BIOS Concepts TSV 行：cid / tid / str / tty / lang。"""
    current_id = ""
    preferred: str | None = None
    terms: list[str] = []
    emitted = 0
    started = False

    def flush() -> BiosConcept | None:
        nonlocal current_id, preferred, terms, emitted
        if not current_id:
            return None
        concept = BiosConcept(
            bios_id=current_id,
            preferred_term=preferred or (terms[0] if terms else None),
            terms=list(dict.fromkeys(terms)),
            external_ids=[],
            semtypes=[],
        )
        current_id = ""
        preferred = None
        terms = []
        emitted += 1
        return concept

    for line in lines:
        if not started:
            started = True
            if line.lower().startswith("cid"):
                continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        cid, label, tty = parts[0], parts[2], parts[3]
        if not cid.startswith("CN"):
            continue
        if cid != current_id:
            done = flush()
            if done is not None:
                yield done
                if max_concepts and emitted >= max_concepts:
                    return
            current_id = cid
        terms.append(label)
        if tty == "PT" and not preferred:
            preferred = label
    done = flush()
    if done is not None:
        yield done


def _open_concepts_stream(cache_dir: Path) -> tuple[Any, Iterator[str]] | None:
    """打开 Concepts TSV：优先已解压 txt，否则 7z 流式解压（免落盘 2.5GB）。"""
    import shutil
    import subprocess

    existing = sorted(cache_dir.glob("Concepts*.txt"))
    if existing:
        fh = existing[0].open(encoding="utf-8", errors="replace")
        return fh, fh

    archives = sorted(cache_dir.glob("Concepts*.7z"))
    if not archives:
        return None
    seven = shutil.which("7z") or shutil.which("7zz")
    if not seven:
        raise RuntimeError("需要 7z 解压 BIOS Concepts*.7z（brew install p7zip）")
    archive = archives[0]
    # 归档内通常只有一个 Concepts*.txt
    proc = subprocess.Popen(
        [seven, "e", "-so", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    return proc, proc.stdout


def _iter_full_concepts(cache_dir: Path, *, max_concepts: int) -> Iterator[BiosConcept]:
    """解析 BIOS 全量布局：优先 Concepts TSV；否则 jsonl；再否则子集。"""
    opened = _open_concepts_stream(cache_dir)
    if opened is not None:
        handle, lines = opened
        try:
            yield from _iter_concepts_tsv_lines(lines, max_concepts=max_concepts)
        finally:
            if hasattr(handle, "kill"):
                handle.kill()
                handle.wait(timeout=30)
            else:
                handle.close()
        return
    candidates = list(cache_dir.rglob("*.jsonl")) + list(cache_dir.rglob("*.json"))
    jsonl = [p for p in candidates if "concept" in p.name.lower() or p.suffix == ".jsonl"]
    if not jsonl:
        yield from load_bios_subset_jsonl(DEFAULT_SUBSET)
        return
    emitted = 0
    for path in jsonl[:3]:
        if path.suffix == ".jsonl":
            for c in load_bios_subset_jsonl(path):
                yield c
                emitted += 1
                if max_concepts and emitted >= max_concepts:
                    return
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rows = data if isinstance(data, list) else data.get("concepts", [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                bid = str(row.get("bios_id") or row.get("id") or row.get("concept_id") or "")
                if not bid:
                    continue
                yield BiosConcept(
                    bios_id=bid,
                    preferred_term=row.get("preferred_term") or row.get("name"),
                    terms=list(row.get("terms") or row.get("synonyms") or []),
                    external_ids=list(row.get("external_ids") or row.get("xrefs") or []),
                    semtypes=list(row.get("semtypes") or row.get("types") or []),
                )
                emitted += 1
                if max_concepts and emitted >= max_concepts:
                    return


def _stream_index_sqlite(
    path: Path, concepts: Iterator[BiosConcept]
) -> tuple[Iterator[BiosConcept], Path]:
    """边遍历边写 sqlite 索引，避免 2e7 概念常驻内存。返回同一流供后续灌库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE ext (external_id TEXT NOT NULL, bios_curie TEXT NOT NULL)")
    conn.execute("CREATE TABLE term (term TEXT NOT NULL, bios_curie TEXT NOT NULL)")

    def gen() -> Iterator[BiosConcept]:
        ext_buf: list[tuple[str, str]] = []
        term_buf: list[tuple[str, str]] = []
        n = 0
        try:
            for c in concepts:
                curie = c.uri_curie
                for xid in c.external_ids:
                    ext_buf.append((xid, curie))
                    ext_buf.append((xid.lower(), curie))
                for t in c.terms + ([c.preferred_term] if c.preferred_term else []):
                    key = (t or "").strip().lower()
                    if key:
                        term_buf.append((key, curie))
                n += 1
                if n % 5000 == 0:
                    if ext_buf:
                        conn.executemany("INSERT INTO ext VALUES (?, ?)", ext_buf)
                        ext_buf.clear()
                    if term_buf:
                        conn.executemany("INSERT INTO term VALUES (?, ?)", term_buf)
                        term_buf.clear()
                    conn.commit()
                yield c
            if ext_buf:
                conn.executemany("INSERT INTO ext VALUES (?, ?)", ext_buf)
            if term_buf:
                conn.executemany("INSERT INTO term VALUES (?, ?)", term_buf)
            conn.commit()
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ext ON ext(external_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_term ON term(term)")
            conn.commit()
        finally:
            conn.close()

    return gen(), path


def initialize_bios(
    *,
    full: bool = True,
    cache_dir: Path | None = None,
    graphdb: GraphDbClient | None = None,
    gate: BiosLicenseGate | None = None,
    cfg: Settings | None = None,
    force: bool = False,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """默认全量流式下载并灌库；subset 仅测试。全量勿 list() 进内存。

    若 ``.initialized`` 已存在且 GraphDB 仍有 Concept，则跳过重灌（``force=True`` 强制）。

    ``on_progress(loaded)``：每写入（或索引）一个 concept 后可选回调（CLI 挂进度条）。
    """
    cfg = cfg or settings
    g = gate or BiosLicenseGate.from_settings(cfg)
    # Settings.bios_init=subset 可强制子集（CI / 无 ACK）
    init_mode = cfg.bios_init if full else "subset"
    if init_mode == "subset":
        full = False

    cache = cache_dir or DEFAULT_CACHE
    max_concepts = _bios_max_concepts(cfg)
    batch_size = _bios_batch_size(cfg)
    idx_path = REPO_ROOT / "data" / "cache" / "bios_ext_index.sqlite"
    client = graphdb or GraphDbClient.from_settings(cfg)
    client.timeout = 600.0

    if not force:
        marker = read_bios_init_marker(cache)
        graph_ready: bool | None = None
        graph_count: int | None = None
        if client.health():
            # 先 COUNT：可识别「marker 被 subset 冲掉但库里仍是全量」
            graph_count = _graph_bios_concept_count(client)
            if graph_count is None:
                graph_ready = _graph_has_bios_concepts(client)
            else:
                graph_ready = graph_count > 0
        if _bios_load_satisfied(
            full=full,
            marker=marker,
            max_concepts=max_concepts,
            graph_ready=graph_ready,
            graph_count=graph_count,
        ):
            marker_concepts = int((marker or {}).get("concepts") or 0)
            concepts_out = (
                int(graph_count)
                if graph_count is not None and graph_count > marker_concepts
                else marker_concepts
            )
            source_out = str((marker or {}).get("source") or "graphdb_cached")
            # 修复被 subset 冲掉的 marker，避免下次再误重灌
            if (
                full
                and graph_count is not None
                and graph_count >= _FULL_GRAPH_MIN_CONCEPTS
                and (marker is None or not _marker_is_full(marker))
            ):
                _write_init_marker(
                    cache,
                    {
                        "source": "full_download_concepts_tsv",
                        "concepts": graph_count,
                        "max_concepts": max_concepts,
                        "license": BIOS_LICENSE,
                        "repaired_from": (marker or {}).get("source"),
                    },
                    force=True,
                )
                source_out = "full_download_concepts_tsv"
            print(
                "[bios] already initialized — skip load "
                f"(marker_concepts={(marker or {}).get('concepts')}, "
                f"graph_count={graph_count}, source={source_out}; "
                "pass force=True / --force to reload)",
                flush=True,
            )
            return {
                "source": source_out,
                "concepts": concepts_out,
                "index": str(idx_path),
                "graph_loaded": concepts_out if graph_ready is not False else 0,
                "cache": str(cache),
                "skipped": True,
            }
        if marker is not None:
            print(
                "[bios] init marker present but not sufficient — reload "
                f"(source={marker.get('source')}, concepts={marker.get('concepts')}, "
                f"graph_count={graph_count})",
                flush=True,
            )

    # 真正灌库前再校验 ACK（跳过路径不要求）
    if full:
        g.require()
        download_bios_full(cache)
        concept_iter: Iterator[BiosConcept] = _iter_full_concepts(cache, max_concepts=max_concepts)
        has_concepts = bool(list(cache.glob("Concepts*.7z")) or list(cache.glob("Concepts*.txt")))
        source = "full_download_concepts_tsv" if has_concepts else "full_download"
        if max_concepts:
            source += f"_capped_{max_concepts}"
    else:
        concept_iter = load_bios_subset_jsonl(DEFAULT_SUBSET)
        source = "subset"

    # 子集金标 xref 始终并进索引（企业 DEMO 映射）
    def merged() -> Iterator[BiosConcept]:
        if full and DEFAULT_SUBSET.exists():
            yield from load_bios_subset_jsonl(DEFAULT_SUBSET)
        yield from concept_iter

    indexed_iter, idx_path = _stream_index_sqlite(idx_path, merged())

    write_alts = cfg.bios_alt_labels
    loaded = 0
    if client.health():
        ensure_repository(client)
        client.clear_graph(GRAPH_BIOMEDICAL)
        batch: list[str] = [
            f"@prefix bios: <{BIOS_NS}> .",
            f"@prefix hmd: <{HMD_NS}> .",
            "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
            "",
        ]
        try:
            for c in indexed_iter:
                iri = f"<{BIOS_NS}{c.bios_id}>"
                batch.append(f"{iri} a skos:Concept ;")
                if c.preferred_term:
                    batch.append(f'  skos:prefLabel "{_esc(c.preferred_term)}" ;')
                if write_alts:
                    for t in c.terms[:5]:
                        if t and t != c.preferred_term:
                            batch.append(f'  skos:altLabel "{_esc(t)}" ;')
                batch.append(f'  hmd:biosId "{_esc(c.bios_id)}" .')
                loaded += 1
                if on_progress is not None:
                    on_progress(loaded)
                if loaded % batch_size == 0:
                    client.load_turtle("\n".join(batch), graph_uri=GRAPH_BIOMEDICAL)
                    batch = batch[:4]
            if len(batch) > 4:
                client.load_turtle("\n".join(batch), graph_uri=GRAPH_BIOMEDICAL)
        except Exception as exc:
            print(f"[bios] ABORT at {loaded}: {exc}", flush=True)
            raise
        if loaded < 10 and full:
            source = "full_download_fallback_subset"
    else:
        # 仍耗尽迭代以写完 sqlite
        for _ in indexed_iter:
            loaded += 1
            if on_progress is not None:
                on_progress(loaded)
        source += "+graphdb_skipped"

    _write_init_marker(
        cache,
        {
            "source": source,
            "concepts": loaded,
            "max_concepts": max_concepts,
            "license": BIOS_LICENSE,
        },
        force=force,
    )
    return {
        "source": source,
        "concepts": loaded,
        "index": str(idx_path),
        "graph_loaded": loaded if client.health() else 0,
        "cache": str(cache),
    }


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
