"""公开实体臂：BIOS lookup、resolve surfaces、exact xref / lexical expand。"""

from __future__ import annotations

from biomed_ontology.foundation.bios_lookup import (
    hydrate_search_surfaces,
    lookup_bios_curies,
    surfaces_from_card,
    fetch_bios_card,
    enterprise_bridges_for_ids,
)
from biomed_ontology.foundation.resolve import ResolutionIndex
from biomed_ontology.foundation.world import load_world_model
from biomed_ontology.search.public_assist import PublicLexicalExpand, PublicNenAssist


def test_lookup_bios_by_external_id_subset():
    curies = lookup_bios_curies(external_id="NCBIGene:4233")
    assert "BIOS:MET_DEMO" in curies


def test_fetch_bios_card_subset_offline():
    card = fetch_bios_card(None, "BIOS:MET_DEMO")
    assert card is not None
    assert card.pref_label == "MET"
    surfaces = surfaces_from_card(card)
    assert "MET" in surfaces
    assert any("c-MET" in s or s == "c-MET" for s in surfaces)


def test_hydrate_search_surfaces_from_curie():
    surfaces, cards = hydrate_search_surfaces(
        mention=None,
        external_ids=["NCBIGene:4233"],
        client=None,
        max_surfaces=8,
    )
    assert cards
    assert any("MET" in s for s in surfaces)


def test_enterprise_bridge_from_exact_xref():
    world = load_world_model(bern2_url=None)
    bridges = enterprise_bridges_for_ids(
        world.entities,
        external_ids=["NCBIGene:4233"],
    )
    assert any(b["enterprise_id"] == "HMD:ENT:TGT:MET" for b in bridges)


def test_public_nen_assist_unique_xref():
    world = load_world_model(bern2_url=None)
    assist = PublicNenAssist(world.resolver.index, bern2=None)
    ents = assist.propose_ents("NCBIGene:4233")
    assert ents == ["HMD:ENT:TGT:MET"]


def test_public_nen_assist_related_not_enough_without_exact():
    # related_xrefs 不应进入 by_exact_external
    world = load_world_model(bern2_url=None)
    idx: ResolutionIndex = world.resolver.index
    assert idx.lookup_exact_external("NCBIGene:4233") == ["HMD:ENT:TGT:MET"]


def test_public_lexical_expand_curie():
    lex = PublicLexicalExpand(bern2=None, graphdb=None)
    terms = lex.propose_terms("NCBIGene:4233")
    assert any("MET" in t for t in terms)


def test_resolve_entity_attaches_search_surfaces():
    from biomed_ontology.foundation.api import FoundationApi

    world = load_world_model(bern2_url=None)
    api = FoundationApi(world)
    # 强制 graphdb health false path for card hydrate via subset
    out = api.resolve_entity("NCBIGene:4233")
    hits = out["resolved"]
    assert hits
    # xref 路径应落到 ENT；若落到 candidate 也应有 surfaces
    chosen = next((h for h in hits if h.get("canonical_entity")), hits[0])
    if chosen.get("canonical_entity"):
        assert chosen.get("search_surfaces")
    else:
        assert chosen.get("resolution_method") in {"bern2_candidate", "unmapped", "xref"}
        assert chosen.get("search_surfaces") or chosen.get("bios_bridges")


def test_lookup_bios_concept_api():
    from biomed_ontology.foundation.api import FoundationApi

    world = load_world_model(bern2_url=None)
    api = FoundationApi(world)
    out = api.lookup_bios_concept(external_id="NCBIGene:4233")
    assert out["found"] is True
    assert out["bios_curie"] == "BIOS:MET_DEMO"
    assert "MET" in out["search_surfaces"]
    assert "HMD:ENT:TGT:MET" in out.get("enterprise_bridges", [])


def test_lookup_bios_no_enterprise_bridge():
    from biomed_ontology.foundation.api import FoundationApi

    world = load_world_model(bern2_url=None)
    api = FoundationApi(world)
    out = api.lookup_bios_concept(external_id="CHEBI:DEMO_ASPIRIN")
    assert out["found"] is True
    assert out["bios_curie"] == "BIOS:ASPIRIN_DEMO"
    assert out.get("enterprise_bridges") == []
    assert any("aspirin" in s.casefold() for s in out["search_surfaces"])


def test_resolve_no_ent_still_has_surfaces():
    from biomed_ontology.foundation.api import FoundationApi

    world = load_world_model(bern2_url=None)
    api = FoundationApi(world)
    out = api.resolve_entity("HGNC:DEMO_BTK")
    hits = out["resolved"]
    assert hits
    assert not any(h.get("canonical_entity") for h in hits)
    surfaces = [s for h in hits for s in (h.get("search_surfaces") or [])]
    assert any("btk" in s.casefold() for s in surfaces)


def test_public_lexical_expand_no_ent_curie():
    lex = PublicLexicalExpand(bern2=None, graphdb=None)
    terms = lex.propose_terms("CHEBI:DEMO_ASPIRIN")
    assert any("aspirin" in t.casefold() for t in terms)
