"""Iceberg 表追加写入（PoC：append）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from biomed_ontology.lake.catalog import (
    DOCUMENTS_TABLE,
    EVIDENCE_CHUNKS_TABLE,
    KNOWLEDGE_CLAIMS_TABLE,
    open_catalog,
)

__all__ = ["append_documents", "append_evidence_chunks", "append_knowledge_claims"]


def _append(table_name: str, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    import pyarrow as pa

    cat = open_catalog()
    table = cat.load_table(table_name)
    # 对齐 Iceberg→Arrow schema（required / int32 / float32），避免 from_pylist 推断成 optional/long/double
    schema = table.schema()
    names = [f.name for f in schema.fields]
    payload = [{k: r.get(k) for k in names} for r in rows]
    arrow = pa.Table.from_pylist(payload, schema=schema.as_arrow())
    table.append(arrow)
    return len(rows)


def append_documents(rows: Sequence[dict[str, Any]]) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched = [{**r, "ingested_at": r.get("ingested_at") or now} for r in rows]
    return _append(DOCUMENTS_TABLE, enriched)


def append_evidence_chunks(rows: Sequence[dict[str, Any]]) -> int:
    return _append(EVIDENCE_CHUNKS_TABLE, rows)


def append_knowledge_claims(rows: Sequence[dict[str, Any]]) -> int:
    return _append(KNOWLEDGE_CLAIMS_TABLE, rows)
