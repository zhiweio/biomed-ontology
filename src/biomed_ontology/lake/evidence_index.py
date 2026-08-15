"""Evidence Object → Milvus foundation_evidence（含 chunk/parent/section/entity_ids）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from biomed_ontology.config import settings
from biomed_ontology.lake.claim_bridge import evidence_id_for_chunk

__all__ = ["delete_evidence_by_doc", "upsert_evidence_objects"]

_COLLECTION = "foundation_evidence"
# 占位维：本集合不冒充语义嵌入。真向量在 hmd index → hmd_chunks。
_DIM = 32
_EMBEDDED = False


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    """Read first present field from a mapping or attribute object."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
            continue
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _escape_milvus_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def delete_evidence_by_doc(doc_id: str, *, uri: str | None = None) -> None:
    """删除某文档在 foundation_evidence 中的全部行（孤儿清理）。"""
    if not doc_id:
        return
    from pymilvus import MilvusClient

    uri = uri or settings.milvus_uri
    client = MilvusClient(uri=uri)
    if not client.has_collection(_COLLECTION):
        return
    expr = f'doc_id == "{_escape_milvus_str(doc_id)}"'
    try:
        client.delete(collection_name=_COLLECTION, filter=expr)
    except TypeError:
        client.delete(collection_name=_COLLECTION, expr=expr)
    client.flush(_COLLECTION)


def upsert_evidence_objects(
    chunks: Sequence[Any],
    *,
    uri: str | None = None,
    doc_id: str | None = None,
) -> int:
    """Upsert Evidence Objects；若给 ``doc_id`` 则先按文档删再写（幂等）。"""
    from pymilvus import MilvusClient

    uri = uri or settings.milvus_uri
    client = MilvusClient(uri=uri)
    _ensure_collection(client)
    # 仅显式 doc_id 时按文档清孤儿（lake ingest）；seed sync 不传，避免误删
    if doc_id:
        delete_evidence_by_doc(doc_id, uri=uri)
    rows: list[dict[str, Any]] = []
    for ch in chunks:
        chunk_id = _field(ch, "chunk_id", "evidence_id")
        text = str(_field(ch, "text", "content", "quote", default="") or "")
        entity_ids = list(_field(ch, "entity_ids", "concept_ids", default=[]) or [])[:64]
        vec = [0.0] * _DIM
        rows.append(
            {
                "evidence_id": evidence_id_for_chunk(str(chunk_id)),
                "chunk_id": str(chunk_id)[:128],
                "parent_id": str(_field(ch, "parent_id", default="") or "")[:128],
                "doc_id": str(_field(ch, "doc_id", "document_id", default="") or "")[:256],
                "section_path": str(_field(ch, "section_path", "section", default="") or "")[:1024],
                "node_kind": str(_field(ch, "node_kind", default="") or "")[:64],
                "text": text[:8000],
                "quote": text[:8000],
                "entity_ids": entity_ids,
                "collection": "literature",
                "score": 1.0,
                "dense": vec,
                "embedded": _EMBEDDED,
            }
        )
    if not rows:
        return 0
    # 分批 upsert，最后一次 flush，避免单次超大 payload + 多次 flush
    batch = 256
    for i in range(0, len(rows), batch):
        client.upsert(collection_name=_COLLECTION, data=rows[i : i + batch])
    client.flush(_COLLECTION)
    return len(rows)


def _ensure_collection(client: Any) -> None:
    from pymilvus import DataType

    name = _COLLECTION
    required = {
        "evidence_id",
        "chunk_id",
        "parent_id",
        "doc_id",
        "section_path",
        "node_kind",
        "text",
        "quote",
        "entity_ids",
        "collection",
        "score",
        "dense",
    }
    need = True
    if client.has_collection(name):
        try:
            info = client.describe_collection(name)
            fields = {f["name"] for f in info.get("fields", [])}
            need = not required <= fields
        except Exception:
            need = True
    if not need:
        return
    if client.has_collection(name):
        client.drop_collection(name)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("evidence_id", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=128)
    schema.add_field("parent_id", DataType.VARCHAR, max_length=128)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
    schema.add_field("section_path", DataType.VARCHAR, max_length=1024)
    schema.add_field("node_kind", DataType.VARCHAR, max_length=64)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("quote", DataType.VARCHAR, max_length=8192)
    schema.add_field(
        "entity_ids",
        DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=64,
        max_length=128,
    )
    schema.add_field("collection", DataType.VARCHAR, max_length=64)
    schema.add_field("score", DataType.FLOAT)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=_DIM)
    idx = client.prepare_index_params()
    idx.add_index(field_name="dense", metric_type="IP", index_type="FLAT")
    client.create_collection(name, schema=schema, index_params=idx)
