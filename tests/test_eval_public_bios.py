"""无 ENT / 公开 BIOS 路径：gold + eval_public_bios + demo W3 契约。"""

from __future__ import annotations

from pathlib import Path

from biomed_ontology.eval import eval_public_bios, load_gold
from biomed_ontology.pipeline import build_literature_base
from biomed_ontology.runtime import open_dual_surface
from tests.support.search_fakes import make_searcher

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "ontology" / "examples" / "golden_path" / "public_no_ent"


def _surface():
    kb = build_literature_base(with_graph=False)
    searcher = make_searcher(kb)
    return open_dual_surface(
        literature_kb=kb,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
        searcher=searcher,
    )


def test_public_bios_gold_loads() -> None:
    gold = load_gold("public_bios")
    assert gold.get("lookup_no_ent")
    assert gold.get("lexical_rewrite")
    assert any(c.get("external_id") == "CHEBI:DEMO_ASPIRIN" for c in gold["lookup_no_ent"])


def test_eval_public_bios_ok() -> None:
    surface = _surface()
    ev = eval_public_bios(surface)
    assert ev.ok, ev.failures
    assert ev.total >= 8


def test_resolve_gold_includes_no_ent_curie() -> None:
    gold = load_gold("resolve")
    texts = {c["text"] for c in gold["cases"]}
    assert "CHEBI:DEMO_ASPIRIN" in texts
    assert "NCBIGene:4233" in texts


def test_golden_path_public_no_ent_expected_matches_runtime() -> None:
    import json

    expected = json.loads(GOLDEN.joinpath("expected.json").read_text(encoding="utf-8"))
    surface = _surface()
    card = surface.foundation.lookup_bios_concept(external_id=expected["lookup"]["external_id"])
    assert card["found"] is True
    assert card["bios_curie"] == expected["lookup"]["bios_curie"]
    assert card.get("enterprise_bridges") == expected["lookup"]["enterprise_bridges"]
    surfaces = {s.casefold() for s in card.get("search_surfaces") or []}
    assert any(
        any(want.casefold() in s for s in surfaces)
        for want in expected["lookup"]["search_surfaces_any_of"]
    )

    out = surface.foundation.resolve_entity(expected["resolve"]["text"])
    ent = next(
        (h.get("canonical_entity") for h in out.get("resolved") or [] if h.get("canonical_entity")),
        None,
    )
    assert ent is expected["resolve"]["canonical_entity"]
    hit_surfaces = []
    for h in out.get("resolved") or []:
        hit_surfaces.extend(h.get("search_surfaces") or [])
    assert any(
        any(want.casefold() in s.casefold() for s in hit_surfaces)
        for want in expected["resolve"]["search_surfaces_any_of"]
    )

    search = surface.tools.search_documents(expected["lexical_rewrite"]["query"], top_k=3)
    assert search.get("expansion_source") == expected["lexical_rewrite"]["expansion_source"]
