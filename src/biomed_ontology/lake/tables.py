"""Iceberg 表写入（同 doc_id / document_id 幂等：先删后写）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from biomed_ontology.lake.catalog import (
    DOCUMENTS_TABLE,
    EVIDENCE_CHUNKS_TABLE,
    KNOWLEDGE_CLAIMS_TABLE,
    open_catalog,
)

__all__ = [
    "append_documents",
    "append_evidence_chunks",
    "append_knowledge_claims",
    "replace_rows",
]


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def replace_rows(
    table_name: str,
    filter_column: str,
    filter_value: str,
    rows: Sequence[dict[str, Any]],
) -> int:
    """按列等值删除后追加（空 rows 也删除，保证重跑可清空）。"""
    import pyarrow as pa

    cat = open_catalog()
    table = cat.load_table(table_name)
    expr = f"{filter_column} = '{_escape_literal(filter_value)}'"
    table.delete(expr)
    if not rows:
        return 0
    schema = table.schema()
    names = [f.name for f in schema.fields]
    payload = [{k: r.get(k) for k in names} for r in rows]
    arrow = pa.Table.from_pylist(payload, schema=schema.as_arrow())
    table.append(arrow)
    return len(rows)


def _append(table_name: str, rows: Sequence[dict[str, Any]]) -> int:
    """无键追加（内部/测试用）。生产路径请用 replace_rows。"""
    if not rows:
        return 0
    import pyarrow as pa

    cat = open_catalog()
    table = cat.load_table(table_name)
    schema = table.schema()
    names = [f.name for f in schema.fields]
    payload = [{k: r.get(k) for k in names} for r in rows]
    arrow = pa.Table.from_pylist(payload, schema=schema.as_arrow())
    table.append(arrow)
    return len(rows)


def append_documents(
    rows: Sequence[dict[str, Any]],
    *,
    doc_id: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched = [{**r, "ingested_at": r.get("ingested_at") or now} for r in rows]
    if doc_id:
        return replace_rows(DOCUMENTS_TABLE, "doc_id", doc_id, enriched)
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in enriched:
        did = str(r.get("doc_id") or "")
        if not did:
            continue
        by_doc.setdefault(did, []).append(r)
    n = 0
    for did, group in by_doc.items():
        n += replace_rows(DOCUMENTS_TABLE, "doc_id", did, group)
    return n


def append_evidence_chunks(
    rows: Sequence[dict[str, Any]],
    *,
    document_id: str | None = None,
) -> int:
    if document_id:
        return replace_rows(EVIDENCE_CHUNKS_TABLE, "document_id", document_id, rows)
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        did = str(r.get("document_id") or "")
        if not did:
            continue
        by_doc.setdefault(did, []).append(r)
    n = 0
    for did, group in by_doc.items():
        n += replace_rows(EVIDENCE_CHUNKS_TABLE, "document_id", did, group)
    return n


def append_knowledge_claims(
    rows: Sequence[dict[str, Any]],
    *,
    document_id: str | None = None,
) -> int:
    if document_id:
        return replace_rows(KNOWLEDGE_CLAIMS_TABLE, "document_id", document_id, rows)
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        did = str(r.get("document_id") or "")
        if not did:
            continue
        by_doc.setdefault(did, []).append(r)
    n = 0
    for did, group in by_doc.items():
        n += replace_rows(KNOWLEDGE_CLAIMS_TABLE, "document_id", did, group)
    return n
