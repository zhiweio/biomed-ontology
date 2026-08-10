"""Document Router：格式路由、probe、fallback。"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.config import load_settings
from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse.layout.base import Capability, LayoutBlock, LayoutResult
from biomed_ontology.parse.router import (
    UnsupportedFormat,
    route_and_extract,
    select_backend,
)


class _FakeBackend:
    def __init__(
        self,
        name: str,
        *,
        blocks: int = 20,
        degraded: tuple[Capability, ...] = (),
        fail: bool = False,
    ):
        self.name = name
        self._blocks = blocks
        self._degraded = degraded
        self._fail = fail
        self.calls = 0

    def supports(self, path: Path) -> bool:
        return True

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self.name} failed")
        blocks = tuple(
            LayoutBlock(kind="text", text=f"{self.name}-{i}", page=1, bbox=(0, 0, 10, 10))
            for i in range(self._blocks)
        )
        return LayoutResult(
            blocks=blocks,
            assets_dir=out_dir,
            page_count=1,
            backend=self.name,
            degraded=self._degraded,
        )


def _ctx() -> TraceContext:
    return TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")


def test_office_routes_to_docling(tmp_path: Path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK")
    d = select_backend(path, config=load_settings({"HMD_LAYOUT_BACKEND": "auto"}))
    assert d.backend == "docling"
    assert d.reason == "office_main"


def test_forced_pymupdf_is_rejected(tmp_path: Path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="已废弃"):
        select_backend(path, forced="pymupdf")


def test_unsupported_format(tmp_path: Path):
    path = tmp_path / "a.xyz"
    path.write_text("x")
    with pytest.raises(UnsupportedFormat):
        select_backend(path, config=load_settings({"HMD_LAYOUT_BACKEND": "auto"}))


def test_fallback_chain_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    # minimal valid-ish pdf for probe may fail; force backend skip probe
    cfg = load_settings(
        {
            "HMD_LAYOUT_BACKEND": "pymupdf4llm",
            "HMD_LAYOUT_FALLBACK": "1",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "true",
        }
    )
    fakes = {
        "pymupdf4llm": _FakeBackend("pymupdf4llm", fail=True),
        "docling": _FakeBackend("docling", blocks=12),
        "mineru": _FakeBackend("mineru", blocks=12),
    }

    def _get(name: str | None = None, *, config=None):
        assert name is not None
        return fakes[name]

    monkeypatch.setattr("biomed_ontology.parse.router.get_layout_backend", _get)
    monkeypatch.setattr(
        "biomed_ontology.parse.router.select_backend",
        lambda *a, **k: __import__(
            "biomed_ontology.parse.router", fromlist=["RouteDecision"]
        ).RouteDecision(backend="pymupdf4llm", reason="forced", confidence=1.0),
    )
    result, trace = route_and_extract(path, tmp_path / "out", ctx=_ctx(), config=cfg)
    assert result.backend == "docling"
    assert trace.chosen == "docling"
    assert fakes["pymupdf4llm"].calls == 1
    assert fakes["docling"].calls == 1


def test_fallback_off_rethrows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    cfg = load_settings(
        {
            "HMD_LAYOUT_BACKEND": "docling",
            "HMD_LAYOUT_FALLBACK": "0",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "true",
        }
    )
    fake = _FakeBackend("docling", fail=True)
    monkeypatch.setattr("biomed_ontology.parse.router.get_layout_backend", lambda *a, **k: fake)
    monkeypatch.setattr(
        "biomed_ontology.parse.router.select_backend",
        lambda *a, **k: __import__(
            "biomed_ontology.parse.router", fromlist=["RouteDecision"]
        ).RouteDecision(backend="docling", reason="forced", confidence=1.0),
    )
    with pytest.raises(RuntimeError, match="failed"):
        route_and_extract(path, tmp_path / "out", ctx=_ctx(), config=cfg)
