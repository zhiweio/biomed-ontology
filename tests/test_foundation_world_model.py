"""Enterprise Biomedical World Model — Foundation 验收测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.bios import (
    BiosLicenseGate,
    build_external_id_index,
    load_bios_subset_jsonl,
)
from biomed_ontology.foundation.ids import (
    EnterpriseKind,
    EvidenceId,
    is_enterprise_id,
    is_evidence_id,
    mint_enterprise_id,
    normalize_evidence_id,
)
from biomed_ontology.foundation.world import load_world_model

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "data" / "foundation"


def test_enterprise_id_not_bios() -> None:
    eid = mint_enterprise_id(EnterpriseKind.DrugCandidate, "savolitinib")
    assert str(eid) == "HMD:ENT:DC:savolitinib"
    assert is_enterprise_id(str(eid))
    assert not str(eid).startswith("BIOS:")


def test_proprietary_dictionary_100_percent() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    proprietary = [
        "HMPL-504",
        "AZD6094",
        "AZD-6094",
        "volitinib",
        "ORPATHYS",
        "沃瑞沙",
        "赛沃替尼",
        "EXP-2025-012",
    ]
    for mention in proprietary:
        out = api.resolve_entity(mention)
        hit = out["resolved"][0]
        assert hit["canonical_entity"], f"未解析：{mention}"
        assert hit["confidence"] == 1.0
        assert hit["resolution_method"] in {"dictionary", "enterprise_id", "xref"}


def test_evidence_id_normalization() -> None:
    assert normalize_evidence_id("PMID:00000001") == "pubmed:00000001"
    assert normalize_evidence_id("EXP-2025-012") == "eln:EXP-2025-012"
    assert normalize_evidence_id("lims:ASY-001") == "lims:ASY-001"
    assert normalize_evidence_id("US20260000001") == "patent:US20260000001"
    assert normalize_evidence_id("ev:lit:savo_met_1") == "ev:lit:savo_met_1"
    assert is_evidence_id("pubmed:123")
    assert str(EvidenceId("PMID:42")) == "pubmed:42"
    assert not is_enterprise_id("pubmed:123")


def test_golden_path_candidate_to_asset() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    result = api.golden_path("HMPL-504")
    assert result["ok"] is True
    assert result["canonical_entity"] == "HMD:ENT:DC:savolitinib"
    ctx = result["context"]
    assert any(t["id"] == "HMD:ENT:TGT:MET" for t in ctx["targets"])
    assert any(d["id"] == "HMD:ENT:IND:nsclc" for d in ctx["diseases"])
    assert any(e.get("span") for e in ctx["evidence"]), "证据必须带 span"
    assert any(e.get("claim") for e in ctx["evidence"]), "Citationware 需要 claim"
    assert any("exp_2025_012" in (a.get("id") or "") for a in ctx["internal_assets"])
    assert any("asy_001" in (a.get("id") or "") for a in ctx["internal_assets"])
    # 向后兼容
    kinds = {e["entity_kind"] for e in ctx["related_entities"]}
    assert "Target" in kinds
    assert any("exp_2025_012" in a["asset_fqn"] for a in ctx["assets"])


def test_evidence_first_requires_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMD_EVIDENCE_BACKEND", "yaml")
    api = FoundationApi(load_world_model(FOUNDATION))
    out = api.search_evidence(entity_ids=["HMD:ENT:DC:savolitinib"], require_quote=True)
    assert out["policy"] == "evidence_first"
    assert out["backend"] == "yaml"
    assert out["evidence"]
    assert all(e.get("quote") for e in out["evidence"])


def test_evolve_mine_writes_candidates_only(tmp_path: Path) -> None:
    from biomed_ontology.foundation.evolve import mine_unmapped_candidates

    result = mine_unmapped_candidates(
        ["unknownzyme-xyz-999", "HMPL-504"],
        out_dir=tmp_path,
    )
    assert result.signals >= 1
    text = result.kgcl_path.read_text(encoding="utf-8")
    assert "TODO curate" in text
    assert "禁止自动写入" in text
    assert result.json_path.exists()


def test_zingg_matches_file_present() -> None:
    path = FOUNDATION / "zingg_matches.jsonl"
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines


def test_claims_carry_provenance() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    rel = api.get_relationships("HMD:ENT:DC:savolitinib")
    assert rel["claims"]
    for c in rel["claims"]:
        assert c.get("source_id")
        assert c.get("source_type")
        assert c.get("extracted_by")


def test_bios_license_gate_blocks_by_default() -> None:
    gate = BiosLicenseGate()
    with pytest.raises(PermissionError, match="CC-BY-NC-ND"):
        gate.require()
    BiosLicenseGate(acknowledged=True, purpose="poc").require()


def test_bios_subset_external_index() -> None:
    concepts = list(load_bios_subset_jsonl(FOUNDATION / "bios_subset.jsonl"))
    idx = build_external_id_index(concepts)
    assert "BIOS:MET_DEMO" in idx.lookup_external("HGNC:7029")
    assert "BIOS:SAVO_DEMO" in idx.lookup_external("DrugBank:DEMO_SAVO")


def test_get_entity_context_hides_backend_names() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    ctx = api.get_entity_context("HMD:ENT:DC:savolitinib")
    blob = str(ctx)
    # Semantic API 载荷不应要求调用方拼 SPARQL
    assert "SELECT " not in blob
    assert ctx["entity"]["enterprise_id"] == "HMD:ENT:DC:savolitinib"
    assert ctx["targets"]
    assert ctx["diseases"]
    assert ctx["internal_assets"]


def test_golden_path_rich_render_smoke() -> None:
    from io import StringIO

    from rich.console import Console

    from biomed_ontology.foundation.render import render_golden_path

    api = FoundationApi(load_world_model(FOUNDATION))
    result = api.golden_path("HMPL-504")
    assert result.get("resolve")
    buf = StringIO()
    cons = Console(file=buf, force_terminal=True, width=100, color_system=None)
    render_golden_path(result, console=cons, verbose=True)
    text = buf.getvalue()
    assert "HMD:ENT:DC:savolitinib" in text
    assert "Trace" in text
    assert "MET" in text
    assert "Citationware" in text or "Evidence" in text
    assert "asliva.eln.exp_2025_012" in text or "EXP-2025-012" in text


def test_foundation_mcp_exposes_get_entity_context() -> None:
    import asyncio

    from biomed_ontology.foundation.mcp import create_foundation_mcp

    tools = asyncio.run(create_foundation_mcp().list_tools())
    names = {t.name for t in tools}
    assert "get_entity_context" in names
    assert "resolve_entity" in names
    assert "graph_sparql" not in names
    assert "vector_search" not in names


def test_claims_use_tested_in_not_inverted_supported_by() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    rel = api.get_relationships("HMD:ENT:DC:savolitinib")
    predicates = {c["predicate"] for c in rel["claims"]}
    assert "testedIn" in predicates
    assert "hasAssay" in predicates
    inverted = [
        c
        for c in rel["claims"]
        if c["predicate"] == "supportedBy" and c.get("object_id", "").startswith("HMD:ENT:DC:")
    ]
    assert not inverted, "supportedBy 不得倒置为企业实体作为 object"
