"""PyMuPDF4LLM Fast Path：拿真 PDF 字节测，但 PDF 由测试现场生成（不入 git）。

重点是**能力缺失必须被声明**。一个后端悄悄少给了公式或 OCR 文本，
比它直接报错更难排查 —— 因为语料看起来是完整的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse import build_tree
from biomed_ontology.parse.layout.pymupdf4llm import PyMuPDF4LLMBackend

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")


def _ctx() -> TraceContext:
    return TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")


def _make_pdf(path: Path, pages: list[list[tuple[str, float, bool]]]) -> Path:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 72.0
        for text, size, bold in lines:
            font = "Times-Bold" if bold else "Times-Roman"
            page.insert_text((72, y), text, fontsize=size, fontname=font)
            y += size * 2.2
    doc.save(path)
    doc.close()
    return path


BODY = 10.0
DOC = [
    [
        ("Savolitinib in Pulmonary Sarcomatoid Carcinoma", 18.0, True),
        ("Abstract", 14.0, True),
        ("Savolitinib is a selective MET tyrosine kinase inhibitor.", BODY, False),
        ("The objective response rate was assessed centrally.", BODY, False),
    ],
    [
        ("Methods", 14.0, True),
        ("Eligible patients received savolitinib 600 mg once daily.", BODY, False),
        ("Results", 14.0, True),
        ("Grade 3 or worse events occurred in 41 percent of patients.", BODY, False),
    ],
]


def test_real_pdf_yields_a_navigable_tree(tmp_path: Path):
    pdf = _make_pdf(tmp_path / "a.pdf", DOC)
    result = PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=_ctx())
    assert result.backend == "pymupdf4llm"
    skeleton, leaves = build_tree(result, toc=[])

    paths = {s.section_path for s in skeleton}
    titles = {p.split(" / ")[-1] for p in paths}
    body = " ".join(n.text for n in leaves)
    assert "600 mg" in body or "Abstract" in titles
    assert result.blocks


def test_page_numbers_are_one_based(tmp_path: Path):
    pdf = _make_pdf(tmp_path / "a.pdf", DOC)
    result = PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=_ctx())
    assert min(b.page for b in result.blocks) == 1
    assert result.page_count == 2


def test_bboxes_are_real_coordinates_not_placeholders(tmp_path: Path):
    """引用要能定位到页面位置，坐标是 Citationware 的地基。"""
    pdf = _make_pdf(tmp_path / "a.pdf", DOC)
    result = PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=_ctx())
    boxed = [b for b in result.blocks if b.bbox]
    assert boxed, "page_chunks 应提供 bbox"
    for block in boxed:
        assert len(block.bbox) == 4
        x0, y0, x1, y1 = block.bbox
        assert x1 > x0 and y1 > y0


def test_scanned_page_declares_missing_ocr(tmp_path: Path):
    """有像素没文本 —— Fast Path 不做 OCR，这件事必须写进 degraded。"""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64))
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "scan.pdf"
    doc.save(pdf)
    doc.close()

    result = PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=_ctx())
    assert "ocr" in result.degraded


def test_degradation_is_recorded_as_a_decision(tmp_path: Path):
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64))
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "scan.pdf"
    doc.save(pdf)
    doc.close()

    ctx = _ctx()
    PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=ctx)
    assert any(d.rule_id == "layout.degraded" for d in ctx.decisions)


def test_page_limit_is_enforced(tmp_path: Path):
    pdf = _make_pdf(tmp_path / "big.pdf", [[("x", BODY, False)]] * 5)
    with pytest.raises(ValueError, match="超过上限"):
        PyMuPDF4LLMBackend(max_pages=3).extract(pdf, tmp_path / "assets", ctx=_ctx())


def test_size_limit_is_enforced(tmp_path: Path):
    pdf = _make_pdf(tmp_path / "a.pdf", DOC)
    with pytest.raises(ValueError, match="超过上限"):
        PyMuPDF4LLMBackend(max_bytes=10).extract(pdf, tmp_path / "assets", ctx=_ctx())


def test_embedded_toc_is_used_when_present(tmp_path: Path):
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), "body text", fontsize=BODY)
    doc.set_toc([[1, "Introduction", 1], [1, "Conclusion", 3]])
    pdf = tmp_path / "toc.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        toc = d.get_toc()
    result = PyMuPDF4LLMBackend().extract(pdf, tmp_path / "assets", ctx=_ctx())
    skeleton, _ = build_tree(result, toc=toc)
    assert {"Introduction", "Conclusion"} <= {s.title for s in skeleton}
