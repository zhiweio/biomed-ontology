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
    is_enterprise_id,
    mint_enterprise_id,
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


def test_golden_path_candidate_to_asset() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    result = api.golden_path("HMPL-504")
    assert result["ok"] is True
    assert result["canonical_entity"] == "HMD:ENT:DC:savolitinib"
    ctx = result["context"]
    kinds = {e["entity_kind"] for e in ctx["related_entities"]}
    assert "Target" in kinds
    assert "Indication" in kinds or any(
        c["object_id"] == "HMD:ENT:IND:nsclc" for c in ctx["relationships"]
    )
    assert any(e.get("quote") for e in ctx["evidence"]), "证据必须带 quote"
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
