"""Zingg 批作业 I/O：物化 enterprise/observation parquet，导出 matches JSONL。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from biomed_ontology.foundation.ids import normalize_alias_key
from biomed_ontology.foundation.paths import REPO_ROOT, ZINGG_MATCHES_PATH

__all__ = [
    "ZinggMaterializeResult",
    "export_matches",
    "materialize",
    "scan_er_observations",
]

ZINGG_DIR = REPO_ROOT / "data" / "zingg"
INPUT_DIR = ZINGG_DIR / "input"
REPORTS_DIR = ZINGG_DIR / "reports"
BOOTSTRAP_PAIRS = ZINGG_DIR / "bootstrap_pairs.jsonl"


@dataclass
class ZinggMaterializeResult:
    enterprise_path: Path
    observation_path: Path
    enterprise_rows: int
    observation_rows: int
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _variant_labels(label: str) -> list[str]:
    """受控变体：大小写 / 去连字符 / 去空格（用于 bootstrap）。"""
    base = label.strip()
    if not base:
        return []
    out = {base, base.lower(), base.upper(), base.replace("-", ""), base.replace(" ", "")}
    if "-" in base:
        out.add(base.replace("-", " "))
    return [x for x in out if x and normalize_alias_key(x) != normalize_alias_key(base)]


def materialize_enterprise(world: Any | None = None) -> list[dict[str, Any]]:
    if world is None:
        from biomed_ontology.foundation.world import load_world_model

        world = load_world_model()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for eid, ent in world.entities.items():
        surfaces = [
            ent.preferred_label_en,
            ent.preferred_label_zh,
            *list(ent.aliases or []),
        ]
        for label in surfaces:
            if not label:
                continue
            key = (eid, normalize_alias_key(label))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "record_id": eid,
                    "side": "enterprise",
                    "label": label,
                    "kind": ent.entity_kind,
                    "external_id": (ent.exact_match_xrefs or [None])[0],
                }
            )
    # resolver alias 倒排（含 dictionary / catalog 回填）
    index = getattr(getattr(world, "resolver", None), "index", None)
    by_alias = getattr(index, "by_alias", None) or {}
    for alias_key, eids in by_alias.items():
        for eid in eids:
            key = (str(eid), str(alias_key))
            if key in seen:
                continue
            seen.add(key)
            ent = world.entities.get(str(eid))
            rows.append(
                {
                    "record_id": str(eid),
                    "side": "enterprise",
                    "label": str(alias_key),
                    "kind": ent.entity_kind if ent else None,
                    "external_id": None,
                }
            )
    return rows


def _bootstrap_observations(enterprise_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if BOOTSTRAP_PAIRS.exists():
        for line in BOOTSTRAP_PAIRS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            mention = str(row.get("mention") or row.get("label") or "").strip()
            if not mention:
                continue
            key = normalize_alias_key(mention)
            rid = hashlib.sha1(f"bootstrap|{key}".encode()).hexdigest()[:16]
            rows.append(
                {
                    "record_id": rid,
                    "side": "observation",
                    "label": mention,
                    "source": "bootstrap",
                    "occurrences": int(row.get("occurrences") or 1),
                    "kind_hint": row.get("kind_hint"),
                }
            )
        return rows
    # 从 enterprise aliases 生成少量变体
    for er in enterprise_rows[:200]:
        for v in _variant_labels(str(er["label"])):
            key = normalize_alias_key(v)
            rid = hashlib.sha1(f"bootstrap|{key}".encode()).hexdigest()[:16]
            rows.append(
                {
                    "record_id": rid,
                    "side": "observation",
                    "label": v,
                    "source": "bootstrap",
                    "occurrences": 1,
                    "kind_hint": er.get("kind"),
                }
            )
    return rows


def scan_er_observations(
    *,
    window_days: int | None = None,
    min_occurrences: int | None = None,
    cfg: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """从 Iceberg hmd.er_observations 聚合；失败返回 ([], warnings)。

    窗口 / 频次默认取 ``Settings.zingg_window_days`` / ``zingg_min_occurrences``。
    """
    from biomed_ontology.config import settings as _settings

    win = _settings.zingg_window_days if window_days is None else int(window_days)
    min_occ = _settings.zingg_min_occurrences if min_occurrences is None else int(min_occurrences)
    warnings: list[str] = []
    try:
        from biomed_ontology.lake.catalog import ER_OBSERVATIONS_TABLE, open_catalog
    except Exception as exc:
        return [], [f"lake import failed: {exc}"]

    try:
        cat = open_catalog(cfg)
        table = cat.load_table(ER_OBSERVATIONS_TABLE)
        arrow = table.scan().to_arrow()
    except Exception as exc:
        return [], [f"er_observations scan failed: {exc}"]

    if arrow is None or arrow.num_rows == 0:
        return [], ["er_observations empty"]

    cutoff = None
    if win > 0:
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=win)).strftime("%Y-%m-%d")

    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    sources: dict[str, Counter[str]] = {}
    kinds: dict[str, Counter[str]] = {}

    cols = arrow.to_pydict()
    n = arrow.num_rows
    for i in range(n):
        status = str((cols.get("resolve_status") or [None])[i] or "")
        if status not in {"unmapped", "low_confidence"}:
            continue
        event_date = str((cols.get("event_date") or [None])[i] or "")
        if cutoff and event_date and event_date < cutoff:
            continue
        mention = str((cols.get("mention") or [None])[i] or "").strip()
        if not mention:
            continue
        key = str((cols.get("mention_key") or [None])[i] or "") or normalize_alias_key(mention)
        counts[key] += 1
        labels.setdefault(key, mention)
        src = str((cols.get("source") or [None])[i] or "runtime_resolve")
        sources.setdefault(key, Counter())[src] += 1
        kh = (cols.get("kind_hint") or [None])[i]
        if kh:
            kinds.setdefault(key, Counter())[str(kh)] += 1

    rows: list[dict[str, Any]] = []
    for key, occ in counts.items():
        if occ < min_occ:
            continue
        primary = sources.get(key, Counter()).most_common(1)
        kind = kinds.get(key, Counter()).most_common(1)
        rid = hashlib.sha1(f"lake|{key}".encode()).hexdigest()[:16]
        rows.append(
            {
                "record_id": rid,
                "side": "observation",
                "label": labels[key],
                "source": primary[0][0] if primary else "mixed",
                "occurrences": occ,
                "kind_hint": kind[0][0] if kind else None,
            }
        )
    return rows, warnings


def materialize(
    *,
    observations: Literal["lake", "bootstrap", "all"] | None = None,
    window_days: int | None = None,
    min_occurrences: int | None = None,
    out_dir: Path | None = None,
) -> ZinggMaterializeResult:
    from biomed_ontology.config import settings as _settings

    obs_mode: Literal["lake", "bootstrap", "all"] = (
        observations if observations is not None else _settings.zingg_observations
    )
    out = Path(out_dir or INPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    sources: list[str] = []

    enterprise_rows = materialize_enterprise()
    obs_rows: list[dict[str, Any]] = []

    if obs_mode in {"lake", "all"}:
        lake_rows, lake_warn = scan_er_observations(
            window_days=window_days, min_occurrences=min_occurrences
        )
        warnings.extend(lake_warn)
        if lake_rows:
            obs_rows.extend(lake_rows)
            sources.append("lake")
        elif obs_mode == "lake":
            warnings.append("lake empty; no observations (use --observations bootstrap|all)")

    if obs_mode in {"bootstrap", "all"} and (obs_mode == "bootstrap" or not obs_rows):
        boot = _bootstrap_observations(enterprise_rows)
        obs_rows.extend(boot)
        sources.append("bootstrap")
        if obs_mode == "all" and "lake" not in sources:
            warnings.append("fell back to bootstrap observations")

    # dedupe by mention_key
    dedup: dict[str, dict[str, Any]] = {}
    for r in obs_rows:
        k = normalize_alias_key(str(r["label"]))
        prev = dedup.get(k)
        if prev is None or int(r.get("occurrences") or 1) > int(prev.get("occurrences") or 1):
            dedup[k] = r
    obs_rows = list(dedup.values())

    ent_path = out / "enterprise.parquet"
    obs_path = out / "observation.parquet"
    _write_parquet(ent_path, enterprise_rows)
    _write_parquet(obs_path, obs_rows)
    return ZinggMaterializeResult(
        enterprise_path=ent_path,
        observation_path=obs_path,
        enterprise_rows=len(enterprise_rows),
        observation_rows=len(obs_rows),
        sources=sources,
        warnings=warnings,
    )


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not rows:
        table = pa.table({"record_id": pa.array([], type=pa.string())})
    else:
        table = pa.Table.from_pylist(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def export_matches(
    *,
    source: Path | None = None,
    out_path: Path | None = None,
    min_score: float | None = None,
    world: Any | None = None,
) -> dict[str, Any]:
    """从 Zingg 原始输出或 fixture JSONL 过滤写入 ontology/mappings/zingg_matches.jsonl。"""
    from biomed_ontology.config import settings
    from biomed_ontology.foundation.world import load_world_model

    threshold = settings.zingg_min_score if min_score is None else float(min_score)
    src = Path(source or (ZINGG_DIR / "raw_matches.jsonl"))
    dest = Path(out_path or ZINGG_MATCHES_PATH)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ambiguous_path = REPORTS_DIR / "ambiguous.jsonl"

    if world is None:
        world = load_world_model()
    known = set(world.entities)

    if not src.exists():
        raise FileNotFoundError(f"zingg raw matches missing: {src}")

    best: dict[str, dict[str, Any]] = {}
    ambiguous: list[dict[str, Any]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        mention = str(row.get("mention") or "").strip()
        eid = str(row.get("enterprise_id") or "").strip()
        score = float(row.get("score") or 0)
        if not mention or not eid or score < threshold:
            continue
        if eid not in known:
            continue
        key = normalize_alias_key(mention)
        prev = best.get(key)
        if prev is None:
            best[key] = {
                "mention": mention,
                "enterprise_id": eid,
                "score": score,
                "source": row.get("source") or "zingg",
                "model_id": row.get("model_id"),
                "z_cluster": row.get("z_cluster"),
            }
            continue
        if score > float(prev["score"]):
            best[key] = {**best[key], **{"mention": mention, "enterprise_id": eid, "score": score}}
        elif score == float(prev["score"]) and prev["enterprise_id"] != eid:
            ambiguous.append(
                {
                    "mention": mention,
                    "a": prev["enterprise_id"],
                    "b": eid,
                    "score": score,
                }
            )
            best.pop(key, None)

    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(v, ensure_ascii=False) for v in best.values()]
    dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    amb_body = "\n".join(json.dumps(a, ensure_ascii=False) for a in ambiguous)
    ambiguous_path.write_text(amb_body + ("\n" if ambiguous else ""), encoding="utf-8")
    return {
        "written": len(best),
        "ambiguous": len(ambiguous),
        "path": str(dest),
        "ambiguous_path": str(ambiguous_path),
        "min_score": threshold,
    }


def link_stub_from_materialized(
    *,
    input_dir: Path | None = None,
    raw_out: Path | None = None,
    score: float = 0.85,
) -> Path:
    """无 Spark 时的联调桩：把 bootstrap 对写成 raw_matches（仅测试/CI）。

    真实生产应跑 docker/zingg Spark link；本函数不替代 Zingg。
    """
    inp = Path(input_dir or INPUT_DIR)
    out = Path(raw_out or (ZINGG_DIR / "raw_matches.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    # 若有 bootstrap_pairs 带 enterprise_id，直接用
    rows: list[dict[str, Any]] = []
    if BOOTSTRAP_PAIRS.exists():
        for line in BOOTSTRAP_PAIRS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("mention") and row.get("enterprise_id"):
                rows.append(
                    {
                        "mention": row["mention"],
                        "enterprise_id": row["enterprise_id"],
                        "score": float(row.get("score") or score),
                        "source": "zingg",
                        "model_id": "stub",
                    }
                )
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    out.write_text(body + ("\n" if rows else ""), encoding="utf-8")
    _ = inp  # reserved for future spark handoff
    return out
