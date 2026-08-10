"""Docling Main Path：用假 Document 测映射，避免 CI 拉模型权重。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse.layout.docling import (
    DoclingBackend,
    _from_docling,
    _strip_docling_image_placeholder,
)


@dataclass
class _Prov:
    page_no: int = 1
    bbox: object = field(default_factory=lambda: SimpleNamespace(l=1.0, t=2.0, r=3.0, b=4.0))


@dataclass
class _Item:
    label: object
    text: str = ""
    prov: list[_Prov] = field(default_factory=lambda: [_Prov()])
    export_calls: list[object] = field(default_factory=list)
    image: object | None = None
    _pil: object | None = None

    def export_to_markdown(self, doc=None):
        self.export_calls.append(doc)
        return "| a | b |\n|---|---|\n| 1 | 2 |"

    def get_image(self, doc=None):
        return self._pil


class _Doc:
    def __init__(self, items: list[tuple[_Item, int]] | None = None) -> None:
        self._items = items or [
            (_Item(label=SimpleNamespace(name="TITLE"), text="Abstract"), 1),
            (_Item(label=SimpleNamespace(name="TEXT"), text="Savolitinib is a MET TKI."), 1),
            (_Item(label=SimpleNamespace(name="TABLE"), text="| a | b |\n|---|---|\n| 1 | 2 |"), 1),
        ]

    def iterate_items(self):
        yield from self._items


def test_docling_maps_items_to_layout_blocks(tmp_path: Path):
    blocks, _degraded, pages = _from_docling(_Doc(), tmp_path, locator_kind="page")
    kinds = {b.kind for b in blocks}
    assert "heading" in kinds and "text" in kinds and "table" in kinds
    assert pages >= 1
    assert any(b.bbox == (1.0, 2.0, 3.0, 4.0) for b in blocks)


def test_docling_table_export_passes_document(tmp_path: Path):
    table = _Item(label=SimpleNamespace(name="TABLE"), text="")
    doc = _Doc([(table, 1)])
    blocks, _degraded, _pages = _from_docling(doc, tmp_path, locator_kind="page")
    assert blocks and blocks[0].kind == "table"
    assert table.export_calls == [doc]


def test_docling_backend_supports_office():
    b = DoclingBackend()
    assert b.supports(Path("a.docx"))
    assert b.supports(Path("a.pptx"))
    assert b.supports(Path("a.xlsx"))
    assert not b.supports(Path("a.csv"))


def test_docling_extract_uses_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("docling")

    class _Conv:
        def convert(self, *a, **k):
            return SimpleNamespace(document=_Doc())

    monkeypatch.setattr(
        "biomed_ontology.parse.layout.docling._document_converter",
        lambda **_kw: _Conv(),
    )
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    ctx = TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")
    result = DoclingBackend().extract(pdf, tmp_path / "assets", ctx=ctx)
    assert result.backend == "docling"
    assert result.blocks


def test_document_converter_disables_torch_compile():
    pytest.importorskip("docling")
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.object_detection_engine_options import (
        TransformersObjectDetectionEngineOptions,
    )

    from biomed_ontology.parse.layout.docling import _document_converter

    conv = _document_converter(render_chart_images=True)
    pdf_opt = conv.format_to_options[InputFormat.PDF]
    engine = pdf_opt.pipeline_options.layout_options.engine_options
    assert isinstance(engine, TransformersObjectDetectionEngineOptions)
    assert engine.compile_model is False
    assert pdf_opt.pipeline_options.generate_picture_images is False


def test_document_converter_enables_office_chart_images():
    pytest.importorskip("docling")
    from docling.datamodel.base_models import InputFormat

    from biomed_ontology.parse.layout.docling import _document_converter

    conv = _document_converter(render_chart_images=True)
    for fmt in (InputFormat.DOCX, InputFormat.PPTX, InputFormat.XLSX):
        opts = conv.format_to_options[fmt].backend_options
        assert opts.render_chart_images is True


def test_strip_docling_image_placeholder_keeps_figure_caption():
    raw = (
        "FIGURE 4\n\n"
        "<!-- 🖼️❌ Image not available. Please use "
        "`PdfPipelineOptions(generate_picture_images=True)` -->"
    )
    assert _strip_docling_image_placeholder(raw) == "FIGURE 4"


def test_picture_placeholder_is_not_used_as_caption(tmp_path: Path):
    placeholder = (
        "<!-- 🖼️❌ Image not available. Please use "
        "`PdfPipelineOptions(generate_picture_images=True)` -->"
    )

    class _Picture(_Item):
        def export_to_markdown(self, doc=None):
            self.export_calls.append(doc)
            return placeholder

    pic = _Picture(label=SimpleNamespace(name="PICTURE"), text="")
    blocks, _degraded, _pages = _from_docling(_Doc([(pic, 1)]), tmp_path, locator_kind="page")
    assert len(blocks) == 1
    assert blocks[0].kind == "image"
    assert blocks[0].text == ""
    assert "generate_picture_images" not in blocks[0].text
    assert blocks[0].asset_path is None


def test_following_caption_attaches_to_empty_picture(tmp_path: Path):
    pic = _Item(label=SimpleNamespace(name="PICTURE"), text="")
    pic.export_to_markdown = lambda doc=None: (  # ty: ignore[invalid-assignment]
        "<!-- Image not available. Please use "
        "`PdfPipelineOptions(generate_picture_images=True)` -->"
    )
    cap = _Item(
        label=SimpleNamespace(name="CAPTION"),
        text="Figure 1. Study schedule overview.",
    )
    blocks, _degraded, _pages = _from_docling(
        _Doc([(pic, 1), (cap, 1)]), tmp_path, locator_kind="page"
    )
    images = [b for b in blocks if b.kind == "image"]
    assert images and images[0].text == "Figure 1. Study schedule overview."
    assert any(b.kind == "text" and "Figure 1" in b.text for b in blocks)


def test_picture_keeps_real_caption_and_exports_pil(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    placeholder = (
        "FIGURE 4\n\n"
        "<!-- Image not available. Please use "
        "`PdfPipelineOptions(generate_picture_images=True)` -->"
    )

    class _Picture(_Item):
        def export_to_markdown(self, doc=None):
            return placeholder

    pil = Image.new("RGB", (8, 8), color=(20, 40, 60))
    pic = _Picture(label=SimpleNamespace(name="PICTURE"), text="", _pil=pil)
    blocks, _degraded, _pages = _from_docling(_Doc([(pic, 1)]), tmp_path, locator_kind="slide")
    assert blocks[0].kind == "image"
    assert blocks[0].text == "FIGURE 4"
    assert blocks[0].asset_path == "images/docling_0000.png"
    assert (tmp_path / blocks[0].asset_path).is_file()
    assert Image.open(tmp_path / blocks[0].asset_path).size == (8, 8)
