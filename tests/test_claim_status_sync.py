"""claim_status：validated 物化 knowledge；extracted 仅 provenance。"""

from __future__ import annotations

from biomed_ontology.foundation.models import KnowledgeClaim
from biomed_ontology.foundation.sync import _claims_turtle
from biomed_ontology.foundation.world import WorldModel


def test_extracted_claim_not_in_knowledge_turtle() -> None:
    wm = WorldModel(
        release_id="t",
        claims=[
            KnowledgeClaim(
                claim_id="claim:v1",
                subject_id="HMD:ENT:DC:savolitinib",
                predicate="targets",
                object_id="HMD:ENT:TGT:MET",
                claim_status="validated",
            ),
            KnowledgeClaim(
                claim_id="claim:x1",
                subject_id="HMD:ENT:DC:savolitinib",
                predicate="inhibits",
                object_id="HMD:ENT:TGT:MET",
                claim_status="extracted",
                confidence=0.7,
            ),
        ],
    )
    know, prov = _claims_turtle(wm)
    assert "hmd:targets" in know
    assert "hmd:inhibits" not in know
    assert 'hmd:claimStatus "validated"' in prov
    assert 'hmd:claimStatus "extracted"' in prov


def test_seed_loads_extracted_claim() -> None:
    from biomed_ontology.foundation.world import load_world_model

    wm = load_world_model()
    extracted = [c for c in wm.claims if c.claim_status == "extracted"]
    validated = [c for c in wm.claims if c.claim_status == "validated"]
    assert extracted, "seed 应含 extracted 样例"
    assert validated
    assert all(c.predicate for c in extracted)
