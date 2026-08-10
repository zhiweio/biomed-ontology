"""claim_status：validated 物化 knowledge；extracted 仅 provenance。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
    GRAPH_PROVENANCE_EXTRACTED,
)
from biomed_ontology.foundation.models import KnowledgeClaim
from biomed_ontology.foundation.sync import _claims_turtle, sync_world_model
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


def test_sync_clears_seed_provenance_not_extracted_graph() -> None:
    """foundation sync 不得 CLEAR provenance_extracted（湖侧幂等结果保留）。"""
    wm = WorldModel(release_id="t", entities={}, claims=[])
    gdb = MagicMock()
    gdb.health.return_value = True
    cleared: list[str] = []

    def _clear(uri: str) -> None:
        cleared.append(uri)

    gdb.clear_graph.side_effect = _clear

    with (
        patch("biomed_ontology.foundation.sync.ensure_repository"),
        patch("biomed_ontology.foundation.sync._upsert_evidence_milvus", return_value=0),
        patch("biomed_ontology.foundation.sync.OpenMetadataClient") as om_cls,
    ):
        om = om_cls.from_settings.return_value
        om.ping.return_value = None
        om.upsert_assets.return_value = 0
        sync_world_model(
            wm,
            graphdb=gdb,
            require_graphdb=True,
            require_milvus=True,
            require_om=True,
        )

    assert GRAPH_ONTOLOGY in cleared
    assert GRAPH_KNOWLEDGE in cleared
    assert GRAPH_PROVENANCE in cleared
    assert GRAPH_PROVENANCE_EXTRACTED not in cleared
