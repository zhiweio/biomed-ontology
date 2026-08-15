"""ER 观测积压：去重、最新状态、resolver overlay、闭环 mapped 事件。

``er_unmapped_backlog`` = 窗口内仍开放的唯一 mention_key 数，不是 Iceberg 行数。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from biomed_ontology.foundation.ids import normalize_alias_key

# 与 evolve.HIGH_CONFIDENCE 对齐；此处不 import evolve，避免与 mine 循环引用
_HIGH_CONFIDENCE = 0.95

__all__ = [
    "CLOSED_STATUSES",
    "OPEN_STATUSES",
    "ErScanResult",
    "aggregate_er_records",
    "close_mapped_er_observations",
    "collect_er_backlog",
    "emit_mapped_mentions",
    "open_er_mentions",
    "scan_er_table",
]

OPEN_STATUSES = frozenset({"unmapped", "low_confidence"})
CLOSED_STATUSES = frozenset({"mapped", "dismissed"})


@dataclass
class ErScanResult:
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    raw_rows: int = 0
    unique_events: int = 0


def _row_id(row: dict[str, Any]) -> str:
    oid = str(row.get("observation_id") or "").strip()
    if oid:
        return oid
    raw = json.dumps(
        {
            "mention": row.get("mention"),
            "mention_key": row.get("mention_key"),
            "event_ts": row.get("event_ts"),
            "source": row.get("source"),
            "resolve_status": row.get("resolve_status"),
            "document_id": row.get("document_id"),
            "chunk_id": row.get("chunk_id"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(raw.encode()).hexdigest()


def _event_ts(row: dict[str, Any]) -> str:
    return str(row.get("event_ts") or row.get("ingested_at") or "")


def _mention_key(row: dict[str, Any]) -> str:
    mention = str(row.get("mention") or row.get("label") or "").strip()
    return str(row.get("mention_key") or "") or normalize_alias_key(mention)


def aggregate_er_records(
    records: list[dict[str, Any]],
    *,
    window_days: int = 0,
    min_occurrences: int = 1,
    now: datetime | None = None,
) -> ErScanResult:
    """按 observation_id 去重，再按 mention_key 取最新状态。

    只返回最新状态仍为 unmapped / low_confidence 的 key。
    ``occurrences`` = 去重后事件数。
    """
    raw_rows = len(records)
    cutoff = None
    if window_days > 0:
        stamp = now or datetime.now(UTC)
        cutoff = (stamp - timedelta(days=int(window_days))).strftime("%Y-%m-%d")

    windowed: list[dict[str, Any]] = []
    for row in records:
        event_date = str(row.get("event_date") or "")
        if cutoff and event_date and event_date < cutoff:
            continue
        mention = str(row.get("mention") or row.get("label") or "").strip()
        if not mention:
            continue
        windowed.append(row)

    deduped: dict[str, dict[str, Any]] = {}
    for row in windowed:
        deduped[_row_id(row)] = row
    unique = list(deduped.values())

    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    sources: dict[str, dict[str, int]] = {}
    kinds: dict[str, dict[str, int]] = {}
    labels: dict[str, str] = {}
    for row in unique:
        key = _mention_key(row)
        counts[key] = counts.get(key, 0) + 1
        labels.setdefault(key, str(row.get("mention") or row.get("label") or "").strip())
        src = str(row.get("source") or "runtime_resolve")
        sources.setdefault(key, {})
        sources[key][src] = sources[key].get(src, 0) + 1
        kh = row.get("kind_hint") or row.get("kind")
        if kh:
            kinds.setdefault(key, {})
            kinds[key][str(kh)] = kinds[key].get(str(kh), 0) + 1
        prev = latest.get(key)
        if prev is None or _event_ts(row) >= _event_ts(prev):
            latest[key] = row

    rows: list[dict[str, Any]] = []
    for key, row in latest.items():
        status = str(row.get("resolve_status") or "")
        if status in CLOSED_STATUSES:
            continue
        if status not in OPEN_STATUSES:
            continue
        occ = int(counts.get(key) or 0)
        if occ < int(min_occurrences):
            continue
        src_counts = sources.get(key) or {}
        kind_counts = kinds.get(key) or {}
        primary = max(src_counts, key=lambda k: src_counts.get(k, 0)) if src_counts else "mixed"
        kind = max(kind_counts, key=lambda k: kind_counts.get(k, 0)) if kind_counts else ""
        rid = hashlib.sha1(f"lake|{key}".encode()).hexdigest()[:16]
        rows.append(
            {
                "id": rid,
                "label": labels.get(key) or key,
                "kind": kind,
                "source": primary,
                "occurrences": occ,
                "mention_key": key,
                "latest_status": status,
                "latest_event_ts": _event_ts(row),
            }
        )
    return ErScanResult(
        rows=rows,
        raw_rows=raw_rows,
        unique_events=len(unique),
    )


def _records_from_arrow(arrow: Any) -> list[dict[str, Any]]:
    cols = arrow.to_pydict()
    n = arrow.num_rows
    keys = list(cols)
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append({k: (cols[k][i] if cols.get(k) is not None else None) for k in keys})
    return out


def scan_er_table(
    *,
    window_days: int | None = None,
    min_occurrences: int | None = None,
    cfg: Any | None = None,
) -> ErScanResult:
    """扫 Iceberg ``hmd.er_observations`` 并聚合成开放 mention。"""
    from biomed_ontology.config import settings as _settings

    win = _settings.zingg_window_days if window_days is None else int(window_days)
    min_occ = _settings.zingg_min_occurrences if min_occurrences is None else int(min_occurrences)
    try:
        from biomed_ontology.lake.catalog import ER_OBSERVATIONS_TABLE, open_catalog
    except Exception as exc:
        return ErScanResult(rows=[], warnings=[f"lake import failed: {exc}"])

    try:
        cat = open_catalog(cfg)
        table = cat.load_table(ER_OBSERVATIONS_TABLE)
        arrow = table.scan().to_arrow()
    except Exception as exc:
        return ErScanResult(rows=[], warnings=[f"er_observations scan failed: {exc}"])

    if arrow is None or arrow.num_rows == 0:
        return ErScanResult(rows=[], warnings=["er_observations empty"], raw_rows=0)

    result = aggregate_er_records(
        _records_from_arrow(arrow),
        window_days=win,
        min_occurrences=min_occ,
    )
    result.raw_rows = int(arrow.num_rows)
    return result


def _hit_mapped(hit: dict[str, Any], *, min_confidence: float) -> bool:
    canon = hit.get("canonical_entity")
    if not canon:
        return False
    return float(hit.get("confidence") or 0.0) >= float(min_confidence)


def open_er_mentions(
    rows: list[dict[str, Any]],
    *,
    world: Any | None = None,
    min_confidence: float = _HIGH_CONFIDENCE,
) -> list[dict[str, Any]]:
    """丢掉当前 World Model 已高置信映射的 mention。"""
    if not rows:
        return []
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model

    api = FoundationApi(world or load_world_model())
    open_rows: list[dict[str, Any]] = []
    for row in rows:
        label = str(row.get("label") or row.get("mention") or "").strip()
        if not label:
            continue
        out = api.resolve_entity(label, emit=False)
        hits = list(out.get("resolved") or [])
        if any(_hit_mapped(h, min_confidence=min_confidence) for h in hits):
            continue
        open_rows.append(row)
    return open_rows


def emit_mapped_mentions(
    mentions: list[str],
    *,
    source: str,
    tool_name: str,
) -> int:
    """状态翻转写 mapped；失败静默，不阻断策展。"""
    n = 0
    try:
        from biomed_ontology.lake.obs_events import emit_er_observation
    except Exception:
        return 0
    for mention in mentions:
        text = str(mention or "").strip()
        if not text:
            continue
        try:
            emit_er_observation(
                mention=text,
                source=source,
                resolve_status="mapped",
                tool_name=tool_name,
            )
            n += 1
        except Exception:
            continue
    return n


def close_mapped_er_observations(*, world: Any | None = None) -> dict[str, Any]:
    """对当前开放 key 静默 resolve，命中则补 mapped 事件。"""
    result = scan_er_table(min_occurrences=1)
    if any("failed" in w for w in result.warnings):
        return {
            "closed": 0,
            "remaining": 0,
            "warnings": list(result.warnings),
        }
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model

    api = FoundationApi(world or load_world_model())
    closed: list[str] = []
    remaining: list[str] = []
    for row in result.rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        out = api.resolve_entity(label, emit=False)
        hits = list(out.get("resolved") or [])
        if any(_hit_mapped(h, min_confidence=_HIGH_CONFIDENCE) for h in hits):
            emit_mapped_mentions([label], source="er_close", tool_name="catalog-publish")
            closed.append(label)
        else:
            remaining.append(label)
    return {"closed": len(closed), "remaining": len(remaining), "warnings": list(result.warnings)}


def collect_er_backlog(
    *,
    world: Any | None = None,
    min_occurrences: int | None = None,
) -> dict[str, Any]:
    """SLO / snapshot 用：开放唯一 key + 事件量信息字段。"""
    try:
        result = scan_er_table(min_occurrences=min_occurrences or 1)
    except Exception:
        return {
            "backlog": None,
            "events": None,
            "raw_rows": None,
            "warnings": ["er_backlog collect failed"],
        }
    if any("failed" in w for w in result.warnings):
        return {
            "backlog": None,
            "events": None,
            "raw_rows": None,
            "warnings": list(result.warnings),
        }
    try:
        open_rows = open_er_mentions(result.rows, world=world)
    except Exception:
        open_rows = list(result.rows)
    return {
        "backlog": len(open_rows),
        "events": result.unique_events,
        "raw_rows": result.raw_rows,
        "warnings": list(result.warnings),
        "rows": open_rows,
    }
