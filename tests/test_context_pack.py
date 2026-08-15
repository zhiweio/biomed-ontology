"""Context Pack 契约字段。"""

from __future__ import annotations

from biomed_ontology.foundation.context_pack import CONTEXT_PACK_VERSION, attach_pack_fields


def test_attach_pack_fields_declares_missing() -> None:
    payload = attach_pack_fields(
        {"ontology_release_id": "0.3.0-ent", "found": False},
        enterprise_id="HMD:ENT:DC:savolitinib",
        entity=None,
        evidence=None,
        assets=None,
        bios_bridges=None,
        found=False,
    )
    assert payload["pack_version"] == CONTEXT_PACK_VERSION
    assert "entity" in payload["missing"]
    assert payload["identity"]["enterprise_id"] == "HMD:ENT:DC:savolitinib"
    assert payload["evidence_tree"] == []


def test_attach_pack_fields_keeps_existing_keys() -> None:
    payload = attach_pack_fields(
        {
            "ontology_release_id": "0.3.0-ent",
            "targets": [{"id": "HMD:ENT:TGT:MET"}],
            "backends": {"entity": "graphdb"},
        },
        enterprise_id="HMD:ENT:DC:savolitinib",
        entity={"entity_kind": "DrugCandidate", "preferred_label_en": "savolitinib"},
        evidence=[{"id": "ev:1"}],
        assets=[{"id": "fqn"}],
        bios_bridges=[{"bios_curie": "BIOS:X"}],
        found=True,
    )
    assert payload["targets"][0]["id"] == "HMD:ENT:TGT:MET"
    assert payload["missing"] == []
    assert payload["license"]["policy"] == "candidate_generation"
