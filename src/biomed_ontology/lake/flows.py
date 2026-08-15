"""Prefect 3 模块级入湖 DAG。业务只在 steps；一篇文档一个失败域。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefect import flow, task

from biomed_ontology.lake.ingest_qa import IngestQAError, run_ingest_qa
from biomed_ontology.lake.steps import (
    IngestContext,
    annotate_bern2,
    load_batch_manifest,
    parse_and_tree,
    put_document,
    register_om_document,
    write_claims,
    write_evidence,
)

__all__ = [
    "document_batch_ingest",
    "document_batch_ingest_flow",
    "document_dual_write_flow",
    "document_ingest",
]


def _result(ctx: IngestContext) -> dict[str, Any]:
    return {
        "source_id": ctx.source_id,
        "doc_id": ctx.doc_id,
        "object_uri": ctx.object_uri,
        "chunk_count": len(ctx.chunks),
        "evidence_n": ctx.evidence_n,
        "claim_n": ctx.claim_n,
        "skipped_claims": ctx.skipped_claims,
        "asset_fqn": ctx.asset_fqn,
        "claim_status": "extracted",
        "errors": list(ctx.errors),
    }


@task(retries=2, retry_delay_seconds=3, tags=["bern2"])
def task_preflight(bern2_url: str | None) -> dict[str, Any]:
    from biomed_ontology.pipelines.preflight import probe_ingest

    return probe_ingest(bern2_url=bern2_url)


@task
def task_put_document(
    ctx: IngestContext,
    file_path: str | None,
    content_type: str,
) -> IngestContext:
    if file_path:
        put_document(ctx, file_path=Path(file_path), content_type=content_type)
    return ctx


@task(retries=2, timeout_seconds=300, tags=["mineru"])
def task_parse_and_tree(
    ctx: IngestContext,
    corpus_yaml: str | None,
    file_path: str | None,
) -> IngestContext:
    parse_and_tree(
        ctx,
        corpus_yaml=Path(corpus_yaml) if corpus_yaml else None,
        file_path=None if corpus_yaml else (Path(file_path) if file_path else None),
    )
    return ctx


@task
def task_ingest_qa(ctx: IngestContext) -> IngestContext:
    ctx.qa = run_ingest_qa(ctx)
    return ctx


@task(retries=2, retry_delay_seconds=5, tags=["bern2"])
def task_annotate_bern2(ctx: IngestContext, bern2_url: str | None) -> IngestContext:
    return annotate_bern2(ctx, bern2_url=bern2_url)


@task
def task_write_evidence(ctx: IngestContext) -> IngestContext:
    return write_evidence(ctx)


@task
def task_write_claims(ctx: IngestContext, bern2_url: str | None) -> IngestContext:
    return write_claims(ctx, bern2_url=bern2_url)


@task
def task_register_om(ctx: IngestContext) -> IngestContext:
    try:
        register_om_document(ctx)
        from biomed_ontology.lake.om_governance import publish_cross_lineage

        publish_cross_lineage(doc_id=ctx.doc_id, asset_fqn=ctx.asset_fqn)
    except Exception as exc:
        ctx.errors.append(f"openmetadata: {exc}")
    return ctx


@flow(name="document_ingest")
def document_ingest(
    *,
    source_id: str,
    doc_id: str,
    file_path: str | None = None,
    corpus_yaml: str | None = None,
    bern2_url: str | None = None,
    register_asset: bool = True,
    content_type: str = "application/pdf",
) -> dict[str, Any]:
    """单文档双写。IngestQA 不过不写 sink。不跑 foundation sync。"""
    task_preflight(bern2_url)
    ctx = IngestContext(source_id=source_id, doc_id=doc_id)
    ctx = task_put_document(ctx, file_path, content_type)
    ctx = task_parse_and_tree(ctx, corpus_yaml, file_path)
    ctx = task_ingest_qa(ctx)
    ctx = task_annotate_bern2(ctx, bern2_url)
    ev = task_write_evidence.submit(ctx)
    cl = task_write_claims.submit(ctx, bern2_url)
    ev_ctx = ev.result()
    cl_ctx = cl.result()
    ctx.evidence_n = ev_ctx.evidence_n
    ctx.claim_n = cl_ctx.claim_n
    ctx.skipped_claims = cl_ctx.skipped_claims
    ctx.claims = cl_ctx.claims
    ctx.errors = list(dict.fromkeys([*ev_ctx.errors, *cl_ctx.errors]))
    if register_asset:
        ctx = task_register_om(ctx)
    return _result(ctx)


def document_dual_write_flow(
    *,
    source_id: str,
    doc_id: str,
    file_path: str | None = None,
    corpus_yaml: str | None = None,
    bern2_url: str | None = None,
    register_asset: bool = True,
) -> dict[str, Any]:
    """CLI 兼容入口：调用模块级 ``document_ingest``。"""
    return document_ingest(
        source_id=source_id,
        doc_id=doc_id,
        file_path=file_path,
        corpus_yaml=corpus_yaml,
        bern2_url=bern2_url,
        register_asset=register_asset,
    )


@flow(name="document_batch_ingest")
def document_batch_ingest(
    *,
    manifest: str,
    bern2_url: str | None = None,
    on_item: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """批量入仓。单篇 Failed 进清单，不把异常当成成功。"""
    items = load_batch_manifest(Path(manifest))
    total = len(items)
    ok: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for i, item in enumerate(items, start=1):
        doc_id = str(item.get("doc_id") or "")
        try:
            result = document_ingest(
                source_id=str(item["source_id"]),
                doc_id=doc_id,
                file_path=item.get("file"),
                corpus_yaml=item.get("corpus_yaml"),
                bern2_url=bern2_url,
                register_asset=bool(item.get("register_asset", True)),
            )
            ok.append(result)
        except IngestQAError as exc:
            quarantined.append(
                {
                    "doc_id": doc_id,
                    "reason": "ingest_qa",
                    "error": str(exc),
                    "retry": {
                        "source_id": item.get("source_id"),
                        "file": item.get("file"),
                        "corpus_yaml": item.get("corpus_yaml"),
                    },
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "doc_id": doc_id,
                    "reason": type(exc).__name__,
                    "error": str(exc),
                    "retry": {
                        "source_id": item.get("source_id"),
                        "file": item.get("file"),
                        "corpus_yaml": item.get("corpus_yaml"),
                    },
                }
            )
        if on_item is not None:
            on_item(i, total)
    return {
        "ok": ok,
        "failed": failed,
        "quarantined": quarantined,
        "ok_n": len(ok),
        "failed_n": len(failed),
        "quarantined_n": len(quarantined),
    }


def document_batch_ingest_flow(
    *,
    manifest: str,
    bern2_url: str | None = None,
    on_item: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """CLI 兼容入口。"""
    return document_batch_ingest(manifest=manifest, bern2_url=bern2_url, on_item=on_item)
