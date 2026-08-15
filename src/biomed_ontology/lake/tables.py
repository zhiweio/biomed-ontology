"""Iceberg 表写入（同 doc_id / document_id 幂等：partial overwrite）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import sleep
from typing import Any, TypeVar

from biomed_ontology.lake.catalog import (
    DOCUMENTS_TABLE,
    ER_OBSERVATIONS_TABLE,
    EVIDENCE_CHUNKS_TABLE,
    INGEST_QUARANTINE_TABLE,
    KNOWLEDGE_CLAIMS_TABLE,
    OBS_TOOL_IO_TABLE,
    open_catalog,
)

__all__ = [
    "append_documents",
    "append_er_observations",
    "append_evidence_chunks",
    "append_ingest_quarantine",
    "append_knowledge_claims",
    "append_obs_tool_io",
    "replace_rows",
]


def _rows_to_arrow(table: Any, rows: Sequence[dict[str, Any]]) -> Any:
    import pyarrow as pa

    schema = table.schema()
    names = [f.name for f in schema.fields]
    payload = [{k: r.get(k) for k in names} for r in rows]
    return pa.Table.from_pylist(payload, schema=schema.as_arrow())


def _filter_exists(table: Any, filt: Any, filter_column: str) -> bool:
    """Cheap existence probe; avoids delete/overwrite on empty matches (UserWarning)."""
    arrow = table.scan(
        row_filter=filt,
        selected_fields=(filter_column,),
        limit=1,
    ).to_arrow()
    return bool(arrow is not None and arrow.num_rows > 0)


_T = TypeVar("_T")


def _catalog_lock_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(
        token in text
        for token in (
            "sqlite_busy",
            "database is locked",
            "commitstateunknown",
            "uncheckedsqlexception",
            "table uuid does not match",
        )
    )


def _with_catalog_retry(op: Callable[[], _T], *, attempts: int = 5) -> _T:
    """SQLite JDBC catalog 在 Connect/并发 commit 下会 SQLITE_BUSY；重载表再试。"""
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return op()
        except Exception as exc:
            last = exc
            if not _catalog_lock_error(exc) or attempt + 1 >= attempts:
                raise
            sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def replace_rows(
    table_name: str,
    filter_column: str,
    filter_value: str,
    rows: Sequence[dict[str, Any]],
    *,
    catalog: Any | None = None,
    table: Any | None = None,
) -> int:
    """按列等值覆盖写入（空 rows 也清空，保证重跑可幂等）。

    对齐 PyIceberg partial overwrite：
    ``table.overwrite(df, overwrite_filter=equalTo(...))``。
    无匹配行时退化为 ``append``（上游对 empty delete 会发 UserWarning）。

    ``catalog`` / ``table`` 可注入以便批量按文档循环时复用连接，避免
    每文档 ``open_catalog()`` 耗尽 FD（Errno 24）。
    """
    from pyiceberg.expressions import EqualTo

    def _write() -> int:
        cat = catalog or open_catalog()
        # 每次 load_table：CommitStateUnknown 后句柄 UUID 会过期
        tbl = cat.load_table(table_name)
        filt = EqualTo(term=filter_column, value=filter_value)
        exists = _filter_exists(tbl, filt, filter_column)

        if not rows:
            if exists:
                tbl.delete(filt)
            return 0

        arrow = _rows_to_arrow(tbl, rows)
        if exists:
            tbl.overwrite(arrow, overwrite_filter=filt)
        else:
            tbl.append(arrow)
        return len(rows)

    return _with_catalog_retry(_write)


def _append(table_name: str, rows: Sequence[dict[str, Any]]) -> int:
    """无键追加（内部/测试用）。生产路径请用 replace_rows。"""
    if not rows:
        return 0

    def _write() -> int:
        table = open_catalog().load_table(table_name)
        table.append(_rows_to_arrow(table, rows))
        return len(rows)

    return _with_catalog_retry(_write)


def _replace_by_key(
    table_name: str,
    filter_column: str,
    key_field: str,
    rows: Sequence[dict[str, Any]],
    *,
    single_key: str | None = None,
) -> int:
    """按业务键分组覆盖；整批共用一个 catalog + table 句柄。"""
    if single_key is not None:
        return replace_rows(table_name, filter_column, single_key, rows)

    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        kid = str(r.get(key_field) or "")
        if not kid:
            continue
        by_key.setdefault(kid, []).append(r)
    if not by_key:
        return 0

    cat = open_catalog()
    table = cat.load_table(table_name)
    n = 0
    for kid, group in by_key.items():
        n += replace_rows(
            table_name,
            filter_column,
            kid,
            group,
            catalog=cat,
            table=table,
        )
    return n


def append_documents(
    rows: Sequence[dict[str, Any]],
    *,
    doc_id: str | None = None,
) -> int:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched = [{**r, "ingested_at": r.get("ingested_at") or now} for r in rows]
    return _replace_by_key(
        DOCUMENTS_TABLE,
        "doc_id",
        "doc_id",
        enriched,
        single_key=doc_id,
    )


def append_evidence_chunks(
    rows: Sequence[dict[str, Any]],
    *,
    document_id: str | None = None,
) -> int:
    from biomed_ontology.lake.connect_admin import paused_iceberg_sinks

    with paused_iceberg_sinks():
        return _replace_by_key(
            EVIDENCE_CHUNKS_TABLE,
            "document_id",
            "document_id",
            rows,
            single_key=document_id,
        )


def append_knowledge_claims(
    rows: Sequence[dict[str, Any]],
    *,
    document_id: str | None = None,
) -> int:
    from biomed_ontology.lake.connect_admin import paused_iceberg_sinks

    with paused_iceberg_sinks():
        return _replace_by_key(
            KNOWLEDGE_CLAIMS_TABLE,
            "document_id",
            "document_id",
            rows,
            single_key=document_id,
        )


def append_obs_tool_io(rows: Sequence[dict[str, Any]]) -> int:
    """观测遥测真 append（仅 Connect/批路径/测试；热路径走 Kafka）。"""
    return _append(OBS_TOOL_IO_TABLE, rows)


def append_er_observations(rows: Sequence[dict[str, Any]]) -> int:
    """ER 缺口事件真 append（仅 Connect/批路径/测试；热路径走 Kafka）。"""
    return _append(ER_OBSERVATIONS_TABLE, rows)


def append_ingest_quarantine(rows: Sequence[dict[str, Any]]) -> int:
    """失败清单按 doc_id 覆盖；Iceberg 不可达时由调用方忽略。"""
    import json

    enriched = []
    for r in rows:
        enriched.append(
            {
                "doc_id": r.get("doc_id"),
                "plane": r.get("plane") or "lake",
                "reason_code": r.get("reason_code") or r.get("reason"),
                "error": r.get("error"),
                "retry_json": json.dumps(r.get("retry") or {}, ensure_ascii=False),
                "prefect_run_id": r.get("prefect_run_id"),
                "first_seen": r.get("first_seen"),
                "last_seen": r.get("last_seen"),
                "status": r.get("status") or "open",
                "replay_count": int(r.get("replay_count") or 0),
            }
        )
    n = 0
    for row in enriched:
        n += replace_rows(INGEST_QUARANTINE_TABLE, "doc_id", str(row["doc_id"]), [row])
    return n
