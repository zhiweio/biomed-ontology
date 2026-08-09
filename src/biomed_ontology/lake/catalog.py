"""Iceberg RestCatalog（与 Trino 共享）。"""

from __future__ import annotations

from typing import Any

from biomed_ontology.config import Settings, settings

__all__ = [
    "DOCUMENTS_TABLE",
    "EVIDENCE_CHUNKS_TABLE",
    "KNOWLEDGE_CLAIMS_TABLE",
    "ensure_lake_tables",
    "open_catalog",
]

NAMESPACE = "hmd"
DOCUMENTS_TABLE = f"{NAMESPACE}.documents"
EVIDENCE_CHUNKS_TABLE = f"{NAMESPACE}.evidence_chunks"
KNOWLEDGE_CLAIMS_TABLE = f"{NAMESPACE}.knowledge_claims"


def open_catalog(cfg: Settings | None = None) -> Any:
    cfg = cfg or settings
    from pyiceberg.catalog import load_catalog

    props = {
        "type": "rest",
        "uri": cfg.iceberg_rest_uri,
        "s3.endpoint": cfg.minio_endpoint
        if cfg.minio_endpoint.startswith("http")
        else f"http://{cfg.minio_endpoint}",
        "s3.access-key-id": cfg.minio_access_key,
        "s3.secret-access-key": cfg.minio_secret_key.get_secret_value(),
        "s3.path-style-access": "true",
        # MinIO 无真实 region；显式 us-east-1 避免 botocore "Unable to resolve region"
        "s3.region": "us-east-1",
        "client.region": "us-east-1",
        "warehouse": f"s3://{cfg.minio_lake_bucket}/",
    }
    return load_catalog("hmd", **props)


def ensure_lake_tables(cfg: Settings | None = None) -> list[str]:
    """创建 namespace 与三表（若不存在）。无 REST 时抛错。"""
    from pyiceberg.schema import NestedField, Schema, StringType, FloatType, IntegerType, ListType

    cat = open_catalog(cfg)
    created: list[str] = []
    try:
        cat.create_namespace(NAMESPACE)
        created.append(f"ns:{NAMESPACE}")
    except Exception:
        pass

    schemas: dict[str, Schema] = {
        "documents": Schema(
            NestedField(1, "doc_id", StringType(), required=True),
            NestedField(2, "object_uri", StringType(), required=True),
            NestedField(3, "source_id", StringType(), required=True),
            NestedField(4, "content_type", StringType(), required=False),
            NestedField(5, "checksum_sha256", StringType(), required=False),
            NestedField(6, "title", StringType(), required=False),
            NestedField(7, "license_tier", StringType(), required=False),
            NestedField(8, "ingested_at", StringType(), required=False),
        ),
        "evidence_chunks": Schema(
            NestedField(1, "chunk_id", StringType(), required=True),
            NestedField(2, "parent_id", StringType(), required=False),
            NestedField(3, "document_id", StringType(), required=True),
            NestedField(4, "section_path", StringType(), required=False),
            NestedField(5, "node_kind", StringType(), required=False),
            NestedField(6, "content", StringType(), required=False),
            NestedField(7, "modality", StringType(), required=False),
            NestedField(8, "page", IntegerType(), required=False),
            NestedField(9, "entity_ids", ListType(10, StringType(), element_required=False), required=False),
            NestedField(11, "milvus_collection", StringType(), required=False),
        ),
        "knowledge_claims": Schema(
            NestedField(1, "claim_id", StringType(), required=True),
            NestedField(2, "subject_id", StringType(), required=True),
            NestedField(3, "predicate", StringType(), required=True),
            NestedField(4, "object_id", StringType(), required=False),
            NestedField(5, "object_value", StringType(), required=False),
            NestedField(6, "claim_status", StringType(), required=True),
            NestedField(7, "confidence", FloatType(), required=False),
            NestedField(8, "evidence_ids", ListType(9, StringType(), element_required=False), required=False),
            NestedField(10, "extracted_by", StringType(), required=False),
            NestedField(11, "span", StringType(), required=False),
            NestedField(12, "document_id", StringType(), required=False),
        ),
    }
    for name, schema in schemas.items():
        ident = (NAMESPACE, name)
        try:
            cat.load_table(ident)
        except Exception:
            cat.create_table(ident, schema=schema)
            created.append(f"{NAMESPACE}.{name}")
    return created
