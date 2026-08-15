"""入仓失败清单：JSONL 为可测 SSOT；Iceberg 可选同步。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biomed_ontology.foundation.paths import REPO_ROOT

__all__ = [
    "QUARANTINE_DIR",
    "claims_path",
    "load_open",
    "mark_replayed",
    "open_path",
    "persist_records",
    "upsert_record",
]


def _base_dir() -> Path:
    import os

    override = os.environ.get("HMD_QUARANTINE_DIR")
    if override:
        return Path(override)
    return REPO_ROOT / "data" / "releases" / "quarantine"


def open_path() -> Path:
    return _base_dir() / "open.jsonl"


def claims_path() -> Path:
    return _base_dir() / "claims.jsonl"


QUARANTINE_DIR = REPO_ROOT / "data" / "releases" / "quarantine"
OPEN_PATH = QUARANTINE_DIR / "open.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("doc_id") or ""), str(row.get("plane") or "lake"))


def _read_all(path: Path | None = None) -> list[dict[str, Any]]:
    dest = path or open_path()
    if not dest.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def _write_all(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    dest = path or open_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    dest.write_text(body, encoding="utf-8")


def persist_records(
    records: list[dict[str, Any]],
    *,
    plane: str,
    run_id: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """把 flow 返回的 failed/quarantined 落盘。同 doc_id+plane 覆盖。"""
    now = _now()
    incoming: list[dict[str, Any]] = []
    for raw in records:
        doc_id = str(raw.get("doc_id") or "")
        if not doc_id:
            continue
        reason = str(raw.get("reason") or raw.get("reason_code") or "unknown")
        incoming.append(
            {
                "doc_id": doc_id,
                "plane": plane,
                "reason_code": reason,
                "error": str(raw.get("error") or ""),
                "retry": dict(raw.get("retry") or {}),
                "prefect_run_id": run_id or str(raw.get("prefect_run_id") or ""),
                "first_seen": str(raw.get("first_seen") or now),
                "last_seen": now,
                "status": "open",
                "replay_count": int(raw.get("replay_count") or 0),
            }
        )
    existing = {_key(r): r for r in _read_all(path)}
    for row in incoming:
        prev = existing.get(_key(row))
        if prev and prev.get("status") == "open":
            row["first_seen"] = prev.get("first_seen") or row["first_seen"]
            row["replay_count"] = int(prev.get("replay_count") or 0)
        existing[_key(row)] = row
    merged = list(existing.values())
    _write_all(merged, path)
    _try_iceberg(incoming)
    return incoming


def load_open(
    *,
    doc_ids: list[str] | None = None,
    reason: str | None = None,
    plane: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    wanted = {d for d in (doc_ids or []) if d}
    out: list[dict[str, Any]] = []
    for row in _read_all(path):
        if row.get("status") != "open":
            continue
        if wanted and row.get("doc_id") not in wanted:
            continue
        if reason and str(row.get("reason_code") or "") != reason:
            continue
        if plane and str(row.get("plane") or "") != plane:
            continue
        out.append(row)
    return out


def upsert_record(row: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    existing = {_key(r): r for r in _read_all(path)}
    existing[_key(row)] = row
    _write_all(list(existing.values()), path)
    return row


def mark_replayed(
    doc_id: str,
    *,
    plane: str,
    error: str | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    rows = _read_all(path)
    found: dict[str, Any] | None = None
    for row in rows:
        if row.get("doc_id") == doc_id and row.get("plane") == plane:
            row["replay_count"] = int(row.get("replay_count") or 0) + 1
            row["last_seen"] = _now()
            if error:
                row["status"] = "open"
                row["error"] = error
            else:
                row["status"] = "replayed"
            found = row
    if found is not None:
        _write_all(rows, path)
    return found


def _try_iceberg(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        from biomed_ontology.lake.tables import append_ingest_quarantine

        append_ingest_quarantine(rows)
    except Exception:
        return
