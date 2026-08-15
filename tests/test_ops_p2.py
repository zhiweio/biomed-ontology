"""P2–P4：HMD_ENV、quarantine/replay、SLO、KGCL/L2、claim promote、证据 join、HGNC。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from biomed_ontology.config import load_settings


def test_hmd_env_default_dev_does_not_warn() -> None:
    closed = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "false"})
    assert closed.env == "dev"
    assert closed.warnings() == []


def test_prod_forbids_stub_warning() -> None:
    s = load_settings(
        {
            "HMD_ENV": "prod",
            "HMD_ZINGG_SKIP_DOCKER": "true",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "false",
        }
    )
    assert s.is_prod
    assert any("stub-link" in w for w in s.warnings())


def test_identity_match_dev_forbidden_in_prod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import biomed_ontology.config as cfg
    from biomed_ontology.pipelines.identity_match import identity_match_dev

    monkeypatch.setenv("PREFECT_HOME", str(tmp_path / "prefect"))
    monkeypatch.setattr(cfg, "settings", load_settings({"HMD_ENV": "prod"}))
    with pytest.raises(RuntimeError, match="forbidden"):
        identity_match_dev()


def test_quarantine_persist_and_replay_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HMD_QUARANTINE_DIR", str(tmp_path))
    from biomed_ontology.lake.quarantine import load_open, mark_replayed, persist_records

    persist_records(
        [
            {
                "doc_id": "DOC:qa",
                "reason": "ingest_qa",
                "error": "empty tree",
                "retry": {"file": "a.pdf"},
            }
        ],
        plane="lake",
    )
    open_rows = load_open(reason="ingest_qa")
    assert len(open_rows) == 1
    assert open_rows[0]["retry"]["file"] == "a.pdf"
    still = mark_replayed("DOC:qa", plane="lake", error="still qa")
    assert still is not None
    assert still["status"] == "open"
    assert still["replay_count"] == 1
    done = mark_replayed("DOC:qa", plane="lake")
    assert done is not None
    assert done["status"] == "replayed"
    assert load_open() == []


def test_replay_empty_filter_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMD_QUARANTINE_DIR", str(tmp_path))
    from biomed_ontology.pipelines.replay import replay_quarantine

    with pytest.raises(RuntimeError, match="no open records"):
        replay_quarantine(doc_ids=["DOC:missing"])


def test_slo_gate_red_on_open_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HMD_QUARANTINE_DIR", str(tmp_path))
    from biomed_ontology.lake.quarantine import persist_records
    from biomed_ontology.pipelines.ops import evaluate_slo

    persist_records([{"doc_id": f"DOC:{i}", "reason": "ingest_qa"} for i in range(3)], plane="lake")
    decision = evaluate_slo(
        {
            "open_quarantine_n": 3,
            "open_quarantine_oldest_age_h": 1,
            "world_model_fingerprint_age_h": 1,
            "release_scorecard_age_h": 1,
            "er_unmapped_backlog": None,
        },
        policy={"ingest_quarantine": {"open_max_docs": 2, "open_max_age_hours": 48}},
    )
    assert decision["ok"] is False
    assert decision["rollback_lake"] is False


def test_zingg_fingerprint_stable(tmp_path: Path) -> None:
    from biomed_ontology.foundation.zingg_io import compute_zingg_input_fingerprint

    ent = tmp_path / "enterprise.parquet"
    obs = tmp_path / "observation.parquet"
    ent.write_bytes(b"abc")
    obs.write_bytes(b"def")
    a = compute_zingg_input_fingerprint(
        enterprise_path=ent,
        observation_path=obs,
        window_days=30,
        observation_rows=2,
        mention_keys=["met", "savo"],
    )
    b = compute_zingg_input_fingerprint(
        enterprise_path=ent,
        observation_path=obs,
        window_days=30,
        observation_rows=2,
        mention_keys=["savo", "met"],
    )
    assert a == b
    c = compute_zingg_input_fingerprint(
        enterprise_path=ent,
        observation_path=obs,
        window_days=7,
        observation_rows=2,
        mention_keys=["met", "savo"],
    )
    assert a != c


def test_kgcl_l1_has_no_todo() -> None:
    from biomed_ontology.foundation.evolve import _kgcl_stub
    from biomed_ontology.foundation.evolve_kgcl import compile_proposal_kgcl

    lines = _kgcl_stub(
        {
            "mention": "c-Met",
            "canonical_entity": "HMD:ENT:TGT:MET",
            "resolution_method": "dictionary",
        }
    )
    assert not any("TODO curate" in ln for ln in lines)
    assert any(ln.startswith("create exact synonym") for ln in lines)
    kgcl = compile_proposal_kgcl(
        {
            "op": "add_xref",
            "mention": "HGNC:7029",
            "xref": "HGNC:7029",
            "target_enterprise_id": "HMD:ENT:TGT:MET",
            "risk_tier": "L2",
        }
    )
    assert kgcl.startswith("create exact match")
    assert "TODO" not in kgcl


def test_l2_xref_apply_does_not_change_enterprise_id(tmp_path: Path) -> None:
    from biomed_ontology.foundation.evolve_apply import apply_approved, save_proposals

    entities = tmp_path / "entities.yaml"
    entities.write_text(
        yaml.safe_dump(
            {
                "entities": [
                    {
                        "enterprise_id": "HMD:ENT:TGT:MET",
                        "entity_kind": "Target",
                        "exact_match_xrefs": ["HGNC:7029"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    props = tmp_path / "props.jsonl"
    save_proposals(
        props,
        [
            {
                "proposal_id": "HMDPROP:l2",
                "mention": "NCBIGene:4233",
                "xref": "NCBIGene:4233",
                "op": "add_xref",
                "write_surface": "entities_xref",
                "target_enterprise_id": "HMD:ENT:TGT:MET",
                "risk_tier": "L2",
                "status": "approved",
            }
        ],
    )
    from biomed_ontology.foundation import evolve_apply as ea

    original = ea.ENTITIES_PATH
    ea.ENTITIES_PATH = entities
    try:
        result = apply_approved(props, dry_run=False)
    finally:
        ea.ENTITIES_PATH = original
    assert any(w["action"] == "append_xref" for w in result.written)
    raw = yaml.safe_load(entities.read_text(encoding="utf-8"))
    ent = raw["entities"][0]
    assert ent["enterprise_id"] == "HMD:ENT:TGT:MET"
    assert ent["entity_kind"] == "Target"
    assert "NCBIGene:4233" in ent["exact_match_xrefs"]


def test_l3_still_skipped(tmp_path: Path) -> None:
    from biomed_ontology.foundation.evolve_apply import apply_approved, save_proposals

    props = tmp_path / "props.jsonl"
    save_proposals(
        props,
        [
            {
                "proposal_id": "HMDPROP:l3",
                "mention": "newgene",
                "op": "create_node",
                "write_surface": "entities_draft",
                "target_enterprise_id": None,
                "risk_tier": "L3",
                "status": "approved",
            }
        ],
    )
    result = apply_approved(props, dry_run=False)
    assert result.written == []
    assert result.skipped


def test_claim_promote_writes_yaml_only(tmp_path: Path) -> None:
    from biomed_ontology.foundation.claim_promote import (
        apply_approved_promotions,
        approve_promotions,
    )

    extracted = tmp_path / "extracted.jsonl"
    extracted.write_text(
        json.dumps(
            {
                "claim_id": "claim:demo_ae",
                "subject_id": "HMD:ENT:DC:savolitinib",
                "predicate": "hasAdverseEvent",
                "object_id": None,
                "claim_status": "extracted",
                "evidence_ids": ["ev:1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims: []\n", encoding="utf-8")
    promotions = tmp_path / "promotions.jsonl"
    from biomed_ontology.foundation.claim_promote import list_extracted

    rows = list_extracted(extracted_path=extracted)
    dest, approved = approve_promotions(
        ["claim:demo_ae"], by="curator", path=promotions, extracted=rows
    )
    assert approved[0]["status"] == "approved"
    out = apply_approved_promotions(dest, claims_path=claims, dry_run=False)
    assert out["graph_insert"] is False
    raw = yaml.safe_load(claims.read_text(encoding="utf-8"))
    assert raw["claims"][0]["claim_status"] == "validated"
    assert raw["claims"][0]["evidence_ids"] == ["ev:1"]


def test_claim_promote_flow_fails_without_approved(tmp_path: Path) -> None:
    from biomed_ontology.pipelines.claims import claim_promote

    empty = tmp_path / "promotions.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no approved"):
        claim_promote(promotions=str(empty), write=True)


def test_evidence_join_by_chunk_id() -> None:
    from biomed_ontology.lake.evidence_join import join_chunks_to_evidence

    joined = join_chunks_to_evidence(
        [{"chunk_id": "CHK:1", "text": "hello", "score": 0.9}],
        [{"chunk_id": "CHK:1", "evidence_id": "ev:1", "entity_ids": ["HMD:ENT:TGT:MET"]}],
    )
    assert joined[0]["joined"] is True
    assert joined[0]["embedded"] is False
    assert joined[0]["evidence_id"] == "ev:1"


def test_context_pack_missing_must_match_empty_slots() -> None:
    from biomed_ontology.foundation.context_eval import eval_context_pack
    from biomed_ontology.foundation.context_pack import attach_pack_fields

    pack = attach_pack_fields(
        {"found": True},
        enterprise_id="HMD:ENT:DC:savolitinib",
        entity={"entity_kind": "DrugCandidate"},
        evidence=[],
        assets=[{"fqn": "asliva.eln.exp_2025_012"}],
        bios_bridges=[{"bios_curie": "BIOS:X"}],
        found=True,
    )
    ev = eval_context_pack(pack)
    assert ev["ok"]
    assert "evidence" in pack["missing"]
    bad = dict(pack)
    bad["missing"] = []
    assert eval_context_pack(bad)["ok"] is False


def test_metric_codes_subset_of_linkml() -> None:
    from biomed_ontology.ontology.metrics import metric_codes_from_vocab, schema_metric_codes

    assert metric_codes_from_vocab() <= schema_metric_codes()
    assert {"ORR", "PFS", "OS", "DCR", "IC50"} <= schema_metric_codes()


def test_hgnc_source_loads_without_download() -> None:
    from biomed_ontology.foundation.biomedical_sources import (
        SOURCE_REGISTRY,
        load_biomedical_source,
    )

    assert "hgnc" in SOURCE_REGISTRY
    out = load_biomedical_source("hgnc", license_ack="")
    assert out["downloaded"] is False
    assert out["enterprise_ids_unchanged"] is True
    assert any(r["xref"] == "HGNC:7029" for r in out["xrefs"])


def test_extraction_respects_doc_type() -> None:
    from biomed_ontology.eval.extraction import eval_extraction
    from biomed_ontology.identity import IdentityService

    ev = eval_extraction(IdentityService.from_catalog().normalizer)
    ids = {c.case_id for c in ev.cases}
    assert "trial_csr_evaluated_in" in ids
    assert "ae_label_no_meddra_knowledge" in ids
    assert ev.negation_ok


def test_compound_seed_is_xref_demo() -> None:
    raw = yaml.safe_load(
        Path("ontology/entities/enterprise_entities.yaml").read_text(encoding="utf-8")
    )
    cmp = next(e for e in raw["entities"] if e["enterprise_id"] == "HMD:ENT:CMP:savo_lot_demo")
    assert cmp["entity_kind"] == "Compound"
    assert "CHEBI:DEMO_SAVO" in cmp["exact_match_xrefs"]


def test_iceberg_catalog_field_names_cover_quarantine() -> None:
    from biomed_ontology.lake.catalog import INGEST_QUARANTINE_TABLE

    assert INGEST_QUARANTINE_TABLE.endswith("ingest_quarantine")


def test_lineage_meta_includes_release() -> None:
    from biomed_ontology.lake.om_governance import runtime_lineage_meta

    meta = runtime_lineage_meta()
    assert "prefect_run_id" in meta
    assert "ontology_release_id" in meta
    assert meta["ontology_release_id"]
