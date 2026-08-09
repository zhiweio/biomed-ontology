"""单文档双写入口（纯函数编排，无 Prefect）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from biomed_ontology.lake.steps import (
    IngestContext,
    annotate_bern2,
    parse_and_tree,
    put_document,
    register_om_document,
    require_bern2,
    write_claims,
    write_evidence,
)

__all__ = ["IngestResult", "ingest_document"]


class IngestResult(dict):
    """便于 JSON 序列化的结果字典。"""


def ingest_document(
    *,
    source_id: str,
    doc_id: str,
    file_path: Path | None = None,
    corpus_yaml: Path | None = None,
    bern2_url: str | None = None,
    register_asset: bool = True,
    content_type: str = "application/pdf",
) -> dict[str, Any]:
    """Document → Evidence∥Claim(extracted)。禁止自动 validated。"""
    require_bern2(bern2_url)
    ctx = IngestContext(source_id=source_id, doc_id=doc_id)
    if file_path is not None:
        put_document(ctx, file_path=file_path, content_type=content_type)
    parse_and_tree(
        ctx,
        corpus_yaml=corpus_yaml,
        file_path=None if corpus_yaml is not None else file_path,
    )
    annotate_bern2(ctx, bern2_url=bern2_url)
    write_evidence(ctx)
    write_claims(ctx, bern2_url=bern2_url)
    if register_asset:
        register_om_document(ctx)
        try:
            from biomed_ontology.lake.om_governance import publish_cross_lineage

            publish_cross_lineage(doc_id=ctx.doc_id, asset_fqn=ctx.asset_fqn)
        except Exception as exc:
            ctx.errors.append(f"openmetadata.lineage: {exc}")
    return IngestResult(
        source_id=ctx.source_id,
        doc_id=ctx.doc_id,
        object_uri=ctx.object_uri,
        chunk_count=len(ctx.chunks),
        evidence_n=ctx.evidence_n,
        claim_n=ctx.claim_n,
        skipped_claims=ctx.skipped_claims,
        asset_fqn=ctx.asset_fqn,
        claim_status="extracted",
        errors=list(ctx.errors),
    )
