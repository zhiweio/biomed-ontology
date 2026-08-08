"""BIOS_v3 下载与初始化。

默认路径：全量从 Hugging Face 下载并灌入 GraphDB。
许可：CC-BY-NC-ND-4.0，必须 HMD_BIOS_LICENSE_ACK。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    def from_env(cls) -> BiosLicenseGate:
        ack = os.environ.get("HMD_BIOS_LICENSE_ACK", "").strip().lower()
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
            "需要 huggingface_hub：uv add huggingface_hub 或 uv sync --extra vector"
        ) from exc
    snapshot_download(
        repo_id=BIOS_HF_REPO,
        repo_type="dataset",
        local_dir=str(dest),
        local_dir_use_symlinks=False,
    )
    return dest


def _bios_max_concepts() -> int:
    """0 = 不截断（全量 ~2.2e7）。默认 0；PoC 可设 HMD_BIOS_MAX_CONCEPTS=50000。"""
    raw = os.environ.get("HMD_BIOS_MAX_CONCEPTS", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _bios_batch_size() -> int:
    raw = os.environ.get("HMD_BIOS_BATCH_SIZE", "500").strip()
    try:
        return max(50, int(raw))
    except ValueError:
        return 500


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
) -> dict[str, Any]:
    """默认全量流式下载并灌库；subset 仅测试。全量勿 list() 进内存。"""
    g = gate or BiosLicenseGate.from_env()
    # CI 可用 HMD_BIOS_INIT=subset 强制子集
    init_mode = os.environ.get("HMD_BIOS_INIT", "full" if full else "subset").lower()
    if init_mode == "subset":
        full = False
    if full:
        g.require()

    cache = cache_dir or DEFAULT_CACHE
    max_concepts = _bios_max_concepts()
    batch_size = _bios_batch_size()
    idx_path = REPO_ROOT / "data" / "cache" / "bios_ext_index.sqlite"

    if full:
        download_bios_full(cache)
        concept_iter: Iterator[BiosConcept] = _iter_full_concepts(
            cache, max_concepts=max_concepts
        )
        has_concepts = bool(
            list(cache.glob("Concepts*.7z")) or list(cache.glob("Concepts*.txt"))
        )
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

    client = graphdb or GraphDbClient(timeout=600.0)
    write_alts = os.environ.get("HMD_BIOS_ALT_LABELS", "0") == "1"
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
                if loaded % batch_size == 0:
                    client.load_turtle("\n".join(batch), graph_uri=GRAPH_BIOMEDICAL)
                    batch = batch[:4]
                    if loaded % (batch_size * 20) == 0:
                        print(f"[bios] loaded {loaded} concepts…", flush=True)
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
        source += "+graphdb_skipped"

    marker = cache / INIT_MARKER
    cache.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "source": source,
                "concepts": loaded,
                "max_concepts": max_concepts,
                "license": BIOS_LICENSE,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
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
