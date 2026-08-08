"""双面运行时装配：open_dual_surface + ToolApi.from_backends。"""

from __future__ import annotations

from biomed_ontology.runtime import DualSurface, open_dual_surface
from biomed_ontology.tools import ToolApi


def test_open_dual_surface_wires_both_apis() -> None:
    surface = open_dual_surface()
    assert isinstance(surface, DualSurface)
    assert surface.tools is not None
    assert surface.foundation is not None
    assert surface.kb is not None
    assert surface.world.release_id


def test_toolapi_from_backends_equiv_from_kb() -> None:
    surface = open_dual_surface()
    via_backends = ToolApi.from_backends(kb=surface.kb)
    via_kb = ToolApi.from_kb(surface.kb)
    assert via_backends.kb is surface.kb
    assert via_kb.kb is surface.kb
    out = via_backends.search_documents("savolitinib", top_k=3)
    assert out["tool_name"] == "search_documents"
    assert "results" in out
