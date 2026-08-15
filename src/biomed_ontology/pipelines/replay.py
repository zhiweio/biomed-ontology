"""按 doc_id / reason 回放入仓失败清单。许可与 IngestQA 失败不自动循环到成功。"""

from __future__ import annotations

from typing import Any

from prefect import flow, task

from biomed_ontology.lake.ingest_qa import IngestQAError
from biomed_ontology.lake.quarantine import load_open, mark_replayed

__all__ = ["replay_quarantine"]


@task
def task_replay_one(row: dict[str, Any], bern2_url: str | None) -> dict[str, Any]:
    plane = str(row.get("plane") or "lake")
    doc_id = str(row["doc_id"])
    retry = dict(row.get("retry") or {})
    try:
        if plane == "literature":
            from biomed_ontology.pipelines.literature import (
                task_literature_qa,
                task_parse_document,
                task_refresh_document,
            )

            item = {
                "doc_id": doc_id,
                "pdf": retry.get("file") or retry.get("pdf"),
                "record": retry.get("record")
                or {"doc_id": doc_id, "pdf": retry.get("file") or retry.get("pdf")},
            }
            source_id = str(retry.get("source_id") or "PUBMED")
            task_parse_document(item, source_id)
            task_literature_qa(doc_id, source_id)
            task_refresh_document(doc_id, str(retry.get("embedder_name") or "multimodal-bio"))
        else:
            from biomed_ontology.lake.flows import document_ingest

            document_ingest(
                source_id=str(retry.get("source_id") or "PUBMED"),
                doc_id=doc_id,
                file_path=retry.get("file"),
                corpus_yaml=retry.get("corpus_yaml"),
                bern2_url=bern2_url,
            )
    except IngestQAError as exc:
        mark_replayed(doc_id, plane=plane, error=str(exc))
        return {"doc_id": doc_id, "status": "open", "reason": "ingest_qa", "error": str(exc)}
    except Exception as exc:
        mark_replayed(doc_id, plane=plane, error=str(exc))
        return {"doc_id": doc_id, "status": "open", "reason": type(exc).__name__, "error": str(exc)}
    mark_replayed(doc_id, plane=plane)
    return {"doc_id": doc_id, "status": "replayed", "plane": plane}


@flow(name="replay_quarantine")
def replay_quarantine(
    *,
    doc_ids: list[str] | None = None,
    reason: str | None = None,
    plane: str | None = None,
    bern2_url: str | None = None,
) -> dict[str, Any]:
    """只重跑 open 记录。IngestQA 失败保持 open，不空转成功。"""
    items = load_open(doc_ids=doc_ids, reason=reason, plane=plane)
    if (doc_ids or reason or plane) and not items:
        raise RuntimeError("replay_quarantine: no open records matching filter")
    ok: list[dict[str, Any]] = []
    still_open: list[dict[str, Any]] = []
    for row in items:
        result = task_replay_one(row, bern2_url)
        if result.get("status") == "replayed":
            ok.append(result)
        else:
            still_open.append(result)
    return {
        "ok": ok,
        "open": still_open,
        "ok_n": len(ok),
        "open_n": len(still_open),
        "auto_loop": False,
    }
