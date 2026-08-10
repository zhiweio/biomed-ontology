"""Prefect Flow：复杂入湖编排（业务逻辑只调 steps）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["document_batch_ingest_flow", "document_dual_write_flow"]


def document_dual_write_flow(
    *,
    source_id: str,
    doc_id: str,
    file_path: str | None = None,
    corpus_yaml: str | None = None,
    bern2_url: str | None = None,
    register_asset: bool = True,
) -> dict[str, Any]:
    """单文档双写 Flow。"""
    from prefect import flow, task

    from biomed_ontology.lake import steps
    from biomed_ontology.lake.ingest import ingest_document

    @task(retries=2, retry_delay_seconds=3)
    def _require_bern2(url: str | None) -> str:
        return steps.require_bern2(url)

    @task
    def _run_ingest(**kwargs: Any) -> dict[str, Any]:
        return ingest_document(**kwargs)

    @flow(name="document_dual_write")
    def _flow() -> dict[str, Any]:
        _require_bern2(bern2_url)
        return _run_ingest(
            source_id=source_id,
            doc_id=doc_id,
            file_path=Path(file_path) if file_path else None,
            corpus_yaml=Path(corpus_yaml) if corpus_yaml else None,
            bern2_url=bern2_url,
            register_asset=register_asset,
        )

    return _flow()


def document_batch_ingest_flow(
    *,
    manifest: str,
    bern2_url: str | None = None,
    on_item: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """批量双写。``on_item(done, total)`` 每处理完一篇后可选回调。"""
    from prefect import flow, task

    from biomed_ontology.lake.steps import load_batch_manifest

    @task(retries=1)
    def _one(item: dict[str, Any]) -> dict[str, Any]:
        return document_dual_write_flow(
            source_id=str(item["source_id"]),
            doc_id=str(item["doc_id"]),
            file_path=item.get("file"),
            corpus_yaml=item.get("corpus_yaml"),
            bern2_url=bern2_url,
            register_asset=bool(item.get("register_asset", True)),
        )

    @flow(name="document_batch_ingest")
    def _flow() -> list[dict[str, Any]]:
        items = load_batch_manifest(Path(manifest))
        total = len(items)
        out: list[dict[str, Any]] = []
        for i, item in enumerate(items, start=1):
            try:
                out.append(_one(item))
            except Exception as exc:
                out.append(
                    {
                        "doc_id": item.get("doc_id"),
                        "error": str(exc),
                        "claim_status": "extracted",
                    }
                )
            if on_item is not None:
                on_item(i, total)
        return out

    return _flow()
