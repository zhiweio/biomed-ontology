"""IngestQA：空树 / 降级 / 未登记来源 / 非法 doc_id。"""

from __future__ import annotations

import pytest

from biomed_ontology.lake.ingest_qa import IngestQAError, run_ingest_qa
from biomed_ontology.lake.steps import IngestContext


def test_empty_tree_blocks() -> None:
    ctx = IngestContext(source_id="PUBMED", doc_id="DOC:ok")
    with pytest.raises(IngestQAError) as exc:
        run_ingest_qa(ctx)
    assert "语义树为空" in str(exc.value)


def test_blank_doc_id_blocks() -> None:
    ctx = IngestContext(source_id="PUBMED", doc_id="  ")
    ctx.chunks = [type("C", (), {"text": "hello"})()]
    with pytest.raises(IngestQAError):
        run_ingest_qa(ctx)


def test_unregistered_source_blocks() -> None:
    ctx = IngestContext(source_id="not-a-real-source", doc_id="DOC:ok")
    ctx.chunks = [type("C", (), {"text": "hello"})()]
    with pytest.raises(IngestQAError) as exc:
        run_ingest_qa(ctx)
    assert "未在 registry 登记" in str(exc.value)


def test_degraded_over_threshold_blocks() -> None:
    ctx = IngestContext(source_id="PUBMED", doc_id="DOC:ok")
    ctx.chunks = [type("C", (), {"text": "hello"})()]
    ctx.parse_degraded = ["bbox", "ocr", "formula"]
    with pytest.raises(IngestQAError) as exc:
        run_ingest_qa(ctx, degraded_threshold=0.4)
    assert "降级比例" in str(exc.value)


def test_healthy_document_passes() -> None:
    ctx = IngestContext(source_id="PUBMED", doc_id="DOC:ok")
    ctx.chunks = [type("C", (), {"text": "hello"})()]
    report = run_ingest_qa(ctx)
    assert report.passed
    assert report.checks["doc_id"] == "DOC:ok"
