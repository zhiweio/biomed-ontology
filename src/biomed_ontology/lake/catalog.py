"""Iceberg RestCatalog（与 Trino 共享）。"""

from __future__ import annotations

from typing import Any

from biomed_ontology.config import Settings, settings

__all__ = [
    "DOCUMENTS_TABLE",
    "ER_OBSERVATIONS_TABLE",
    "EVIDENCE_CHUNKS_TABLE",
    "INGEST_QUARANTINE_TABLE",
    "KNOWLEDGE_CLAIMS_TABLE",
    "OBS_DECISION_TABLE",
    "OBS_SPAN_TABLE",
    "OBS_TOOL_IO_TABLE",
    "ensure_lake_tables",
    "expire_lake_snapshots",
    "open_catalog",
    "scan_obs_table",
]

NAMESPACE = "hmd"
DOCUMENTS_TABLE = f"{NAMESPACE}.documents"
EVIDENCE_CHUNKS_TABLE = f"{NAMESPACE}.evidence_chunks"
KNOWLEDGE_CLAIMS_TABLE = f"{NAMESPACE}.knowledge_claims"
OBS_TOOL_IO_TABLE = f"{NAMESPACE}.obs_tool_io"
OBS_DECISION_TABLE = f"{NAMESPACE}.obs_decision"
OBS_SPAN_TABLE = f"{NAMESPACE}.obs_span"
ER_OBSERVATIONS_TABLE = f"{NAMESPACE}.er_observations"
INGEST_QUARANTINE_TABLE = f"{NAMESPACE}.ingest_quarantine"
_EVENT_DATE_SOURCE_IDS = {
    "er_observations": 17,
    "obs_decision": 17,
    "obs_span": 10,
}


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
    """创建 namespace 与湖表（若不存在）。无 REST 时抛错。"""
    from pyiceberg.schema import FloatType, IntegerType, ListType, NestedField, Schema, StringType

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
            NestedField(
                9, "entity_ids", ListType(10, StringType(), element_required=False), required=False
            ),
            NestedField(11, "milvus_collection", StringType(), required=False),
            NestedField(12, "release_id", StringType(), required=False),
            NestedField(13, "source_id", StringType(), required=False),
            NestedField(14, "license_tier", StringType(), required=False),
            NestedField(15, "sort_order", IntegerType(), required=False),
        ),
        "knowledge_claims": Schema(
            NestedField(1, "claim_id", StringType(), required=True),
            NestedField(2, "subject_id", StringType(), required=True),
            NestedField(3, "predicate", StringType(), required=True),
            NestedField(4, "object_id", StringType(), required=False),
            NestedField(5, "object_value", StringType(), required=False),
            NestedField(6, "claim_status", StringType(), required=True),
            NestedField(7, "confidence", FloatType(), required=False),
            NestedField(
                8, "evidence_ids", ListType(9, StringType(), element_required=False), required=False
            ),
            NestedField(10, "extracted_by", StringType(), required=False),
            NestedField(11, "span", StringType(), required=False),
            NestedField(12, "document_id", StringType(), required=False),
        ),
        "obs_tool_io": Schema(
            NestedField(1, "trace_id", StringType(), required=True),
            NestedField(2, "tool_name", StringType(), required=True),
            NestedField(3, "ontology_release_id", StringType(), required=False),
            NestedField(4, "status", StringType(), required=False),
            NestedField(5, "latency_ms", FloatType(), required=False),
            NestedField(6, "agent_id", StringType(), required=False),
            NestedField(7, "session_id", StringType(), required=False),
            NestedField(8, "input_json", StringType(), required=False),
            NestedField(9, "output_json", StringType(), required=False),
            NestedField(10, "error_message", StringType(), required=False),
            NestedField(11, "contract_valid", StringType(), required=False),
            NestedField(12, "event_ts", StringType(), required=False),
            NestedField(13, "ingested_at", StringType(), required=False),
            NestedField(14, "event_date", StringType(), required=False),
        ),
        "obs_decision": Schema(
            NestedField(1, "decision_id", StringType(), required=True),
            NestedField(2, "trace_id", StringType(), required=True),
            NestedField(3, "step_seq", IntegerType(), required=False),
            NestedField(4, "stage", StringType(), required=False),
            NestedField(5, "justification", StringType(), required=False),
            NestedField(6, "chosen", StringType(), required=False),
            NestedField(7, "span_id", StringType(), required=False),
            NestedField(8, "candidates_json", StringType(), required=False),
            NestedField(9, "state_before", StringType(), required=False),
            NestedField(10, "state_after", StringType(), required=False),
            NestedField(11, "confidence", FloatType(), required=False),
            NestedField(12, "rule_id", StringType(), required=False),
            NestedField(13, "model_id", StringType(), required=False),
            NestedField(14, "elapsed_ms", FloatType(), required=False),
            NestedField(15, "event_ts", StringType(), required=False),
            NestedField(16, "ingested_at", StringType(), required=False),
            NestedField(17, "event_date", StringType(), required=False),
            NestedField(18, "subject_text", StringType(), required=False),
            NestedField(19, "candidates_n", IntegerType(), required=False),
            NestedField(20, "truncated_fields", StringType(), required=False),
        ),
        "obs_span": Schema(
            NestedField(1, "span_id", StringType(), required=True),
            NestedField(2, "trace_id", StringType(), required=True),
            NestedField(3, "name", StringType(), required=False),
            NestedField(4, "parent_id", StringType(), required=False),
            NestedField(5, "duration_ms", FloatType(), required=False),
            NestedField(6, "status", StringType(), required=False),
            NestedField(7, "attributes_json", StringType(), required=False),
            NestedField(8, "event_ts", StringType(), required=False),
            NestedField(9, "ingested_at", StringType(), required=False),
            NestedField(10, "event_date", StringType(), required=False),
            NestedField(11, "truncated_fields", StringType(), required=False),
        ),
        "er_observations": Schema(
            NestedField(1, "observation_id", StringType(), required=True),
            NestedField(2, "mention", StringType(), required=True),
            NestedField(3, "mention_key", StringType(), required=False),
            NestedField(4, "source", StringType(), required=False),
            NestedField(5, "resolve_status", StringType(), required=False),
            NestedField(6, "kind_hint", StringType(), required=False),
            NestedField(7, "confidence", FloatType(), required=False),
            NestedField(8, "tool_name", StringType(), required=False),
            NestedField(9, "trace_id", StringType(), required=False),
            NestedField(10, "document_id", StringType(), required=False),
            NestedField(11, "chunk_id", StringType(), required=False),
            NestedField(
                12, "bern2_ids", ListType(13, StringType(), element_required=False), required=False
            ),
            NestedField(14, "ontology_release_id", StringType(), required=False),
            NestedField(15, "event_ts", StringType(), required=False),
            NestedField(16, "ingested_at", StringType(), required=False),
            NestedField(17, "event_date", StringType(), required=False),
        ),
        "ingest_quarantine": Schema(
            NestedField(1, "doc_id", StringType(), required=True),
            NestedField(2, "plane", StringType(), required=True),
            NestedField(3, "reason_code", StringType(), required=False),
            NestedField(4, "error", StringType(), required=False),
            NestedField(5, "retry_json", StringType(), required=False),
            NestedField(6, "prefect_run_id", StringType(), required=False),
            NestedField(7, "first_seen", StringType(), required=False),
            NestedField(8, "last_seen", StringType(), required=False),
            NestedField(9, "status", StringType(), required=False),
            NestedField(10, "replay_count", IntegerType(), required=False),
        ),
    }
    for name, schema in schemas.items():
        ident = (NAMESPACE, name)
        try:
            table = cat.load_table(ident)
            _ensure_optional_columns(table, schema)
        except Exception:
            extra: dict[str, Any] = {}
            source_id = _EVENT_DATE_SOURCE_IDS.get(name)
            if source_id is not None:
                spec = _event_date_partition_spec(source_id)
                if spec is not None:
                    extra["partition_spec"] = spec
            cat.create_table(ident, schema=schema, **extra)
            created.append(f"{NAMESPACE}.{name}")
    return created


def _event_date_partition_spec(source_id: int = 17) -> Any | None:
    try:
        from pyiceberg.partitioning import PartitionField, PartitionSpec
        from pyiceberg.transforms import IdentityTransform

        return PartitionSpec(
            PartitionField(
                source_id=source_id,
                field_id=1000,
                transform=IdentityTransform(),
                name="event_date",
            )
        )
    except Exception:
        return None


def scan_obs_table(
    table_ident: str,
    *,
    window_days: int = 7,
    cfg: Settings | None = None,
) -> list[dict[str, Any]]:
    """扫观测表并按 ``event_date`` 窗口过滤。湖不可达则抛错。"""
    from datetime import UTC, datetime, timedelta

    cat = open_catalog(cfg)
    arrow = cat.load_table(table_ident).scan().to_arrow()
    if arrow is None or arrow.num_rows == 0:
        return []
    cutoff = None
    if window_days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    cols = arrow.to_pydict()
    rows: list[dict[str, Any]] = []
    for i in range(arrow.num_rows):
        event_date = str((cols.get("event_date") or [None])[i] or "")
        if cutoff and event_date and event_date < cutoff:
            continue
        rows.append({name: cols[name][i] for name in cols})
    return rows


def expire_lake_snapshots(*, older_than_days: int | None = None) -> dict[str, Any]:
    """Iceberg snapshot expire；不是 Spark 作业。open quarantine 不删。"""
    from datetime import UTC, datetime, timedelta

    from biomed_ontology.config import settings

    days = int(older_than_days or settings.lake_observation_retain_days)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    cat = open_catalog()
    expired: dict[str, Any] = {}
    for name in ("er_observations", "obs_tool_io", "obs_decision", "obs_span"):
        try:
            table = cat.load_table((NAMESPACE, name))
            before = len(table.inspect.snapshots())
            table.maintenance.expire_snapshots(older_than=cutoff)
            after = len(table.inspect.snapshots())
            expired[name] = max(0, before - after)
        except Exception as exc:
            expired[name] = -1
            expired[f"{name}_error"] = str(exc)
    return {"older_than_days": days, "expired": expired}


def _ensure_optional_columns(table: Any, want: Any) -> None:
    """已有表补齐可选列（Iceberg schema evolve）；失败则留给 --recreate / 重建。"""
    have = {f.name for f in table.schema().fields}
    missing = [f for f in want.fields if f.name not in have]
    if not missing:
        return
    try:
        with table.update_schema() as update:
            for field in missing:
                update.add_column(field.name, field.field_type, required=False)
    except Exception:
        # 旧 catalog / 权限不足时不阻断启动；写入侧仍按当前 schema 投影字段
        return
