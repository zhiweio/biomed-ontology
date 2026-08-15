"""双证据平面：语义走 hmd_chunks，对象/过滤走 foundation_evidence，按 chunk_id join。"""

from __future__ import annotations

from typing import Any

__all__ = ["join_chunks_to_evidence"]


def join_chunks_to_evidence(
    chunk_hits: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同一 chunk_id；foundation_evidence 不冒充嵌入。"""
    by_chunk: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        cid = str(row.get("chunk_id") or "")
        if cid:
            by_chunk[cid] = row
    out: list[dict[str, Any]] = []
    for hit in chunk_hits:
        cid = str(hit.get("chunk_id") or hit.get("id") or "")
        meta = by_chunk.get(cid) or {}
        merged = dict(meta)
        merged.update(hit)
        merged["chunk_id"] = cid
        merged["joined"] = bool(meta)
        merged["embedded"] = False
        merged["milvus_collection"] = hit.get("milvus_collection") or "hmd_chunks"
        out.append(merged)
    return out
