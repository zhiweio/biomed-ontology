"""纯文本 / Markdown 版面后端。"""

from __future__ import annotations

from pathlib import Path

from biomed_ontology.config import load_settings
from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse.layout.registry import get_layout_backend
from biomed_ontology.parse.layout.text import TextBackend


def _ctx() -> TraceContext:
    return TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")


def test_text_backend_plain_paragraphs(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_text("first para\n\nsecond para\n", encoding="utf-8")
    result = TextBackend().extract(path, tmp_path / "out", ctx=_ctx())
    assert result.backend == "text"
    assert result.page_count == 1
    assert [b.text for b in result.blocks] == ["first para", "second para"]
    assert "bbox" in result.degraded


def test_text_backend_markdown_headings(tmp_path: Path):
    path = tmp_path / "a.md"
    path.write_text("# Title\n\nintro\n\n## Section\nbody\n", encoding="utf-8")
    result = TextBackend().extract(path, tmp_path / "out", ctx=_ctx())
    kinds = [b.kind for b in result.blocks]
    assert kinds[0] == "heading"
    assert result.blocks[0].text == "Title"
    assert result.blocks[0].level == 1
    assert "intro" in [b.text for b in result.blocks]


def test_registry_resolves_text_backend():
    cfg = load_settings({"HMD_LAYOUT_BACKEND": "text"})
    backend = get_layout_backend(config=cfg)
    assert backend.name == "text"
