"""关系融合：互斥数值打 conflict，QualityGate 不自动 validated。"""

from __future__ import annotations

from biomed_ontology._generated.hmd_concept import PredicateEnum
from biomed_ontology.corpus.extract import ExtractedFact, detect_conflicts
from biomed_ontology.quality import QualityGate


def test_detect_conflicts_flags_metric_mismatch() -> None:
    facts = [
        ExtractedFact(
            fact_id="a",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate=PredicateEnum.in_clinical_trial_for,
            object_value="45",
            qualifiers=["metric=ORR"],
        ),
        ExtractedFact(
            fact_id="b",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate=PredicateEnum.in_clinical_trial_for,
            object_value="72",
            qualifiers=["metric=ORR"],
        ),
    ]
    conflicts = detect_conflicts(facts)
    assert conflicts
    assert all("conflict=true" in f.qualifiers for f in facts)


def test_quality_gate_evaluate_claims_blocks_missing_evidence() -> None:
    gate = QualityGate()
    decision = gate.evaluate_claims(
        [
            {
                "claim_id": "c1",
                "subject_id": "HMD:ENT:DC:savolitinib",
                "predicate": "treats",
                "object_id": "HMD:ENT:IND:nsclc",
                "evidence_ids": [],
            }
        ]
    )
    assert not decision.passed
    assert any("fact_without_evidence" in b for b in decision.blocking)
