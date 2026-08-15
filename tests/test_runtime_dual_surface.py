"""双面运行时装配：open_dual_surface + ToolApi.from_backends。"""

from __future__ import annotations

from biomed_ontology.runtime import DualSurface, open_dual_surface
from biomed_ontology.tools import ToolApi
from tests.support.search_fakes import make_searcher


def test_open_dual_surface_wires_both_apis(kb) -> None:
    searcher = make_searcher(kb)
    surface = open_dual_surface(
        literature_kb=kb,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
        searcher=searcher,
    )
    assert isinstance(surface, DualSurface)
    assert surface.tools is not None
    assert surface.foundation is not None
    assert surface.kb is not None
    assert surface.world.release_id
    assert surface.search_backend == "milvus"
    assert surface.identity is not None
    assert surface.identity.resolver is surface.world.resolver


def test_toolapi_from_backends_equiv_from_kb(kb) -> None:
    searcher = make_searcher(kb)
    via_backends = ToolApi.from_backends(kb=kb, backend=searcher.backend, searcher=searcher)
    via_kb = ToolApi.from_kb(kb, backend=searcher.backend, searcher=searcher)
    assert via_backends.kb is kb
    assert via_kb.kb is kb
    out = via_backends.search_documents("savolitinib", top_k=3)
    assert out["tool_name"] == "search_documents"
    assert "results" in out
