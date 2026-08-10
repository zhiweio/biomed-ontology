"""ExtractedFact → KnowledgeClaim(extracted)。"""

from __future__ import annotations

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, PredicateEnum, ReviewStatusEnum
from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
from biomed_ontology.corpus.extract import Evidence, ExtractedFact
from biomed_ontology.lake.claim_bridge import facts_to_claims


def test_facts_to_claims_force_extracted() -> None:
    facts = [
        ExtractedFact(
            fact_id="f1",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate=PredicateEnum.inhibits,
            object_id="HMD:ENT:TGT:MET",
            evidence=[
                Evidence(
                    chunk_id="CHK:txt.abc",
                    doc_id="DOC:1",
                    quote="inhibits MET",
                    modality=ModalityChannelEnum.TEXT,
                )
            ],
            confidence=0.8,
            extractor_id="text-v1",
            review_status=ReviewStatusEnum.PENDING,
            license_tier=LicenseTierEnum.TIER_0,
            modality=ModalityChannelEnum.TEXT,
        )
    ]
    claims, skipped = facts_to_claims(facts, document_id="DOC:1")
    assert skipped == 0
    assert len(claims) == 1
    assert claims[0].claim_status == "extracted"
    assert claims[0].predicate == "inhibits"
    assert claims[0].evidence_ids == ["ev:chunk:CHK:txt.abc"]


def test_unmapped_subject_skipped() -> None:
    facts = [
        ExtractedFact(
            fact_id="f2",
            subject_id="UNKNOWN_DRUG",
            predicate=PredicateEnum.treats,
            object_id="HMD:ENT:IND:nsclc",
            evidence=[],
            confidence=0.5,
            extractor_id="text-v1",
            review_status=ReviewStatusEnum.PENDING,
            license_tier=LicenseTierEnum.TIER_0,
            modality=ModalityChannelEnum.TEXT,
        )
    ]
    claims, skipped = facts_to_claims(facts, document_id="DOC:1", resolve_fn=lambda _: None)
    assert claims == []
    assert skipped == 1
