"""Enterprise Biomedical World Model — Foundation 验收测试。

查询路径强制 GraphDB / Milvus / OpenMetadata；YAML 仅 seed 资源。
无后端时联调类测试 skip，不回落 YAML。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from biomed_ontology.foundation.api import BackendUnavailableError, FoundationApi
from biomed_ontology.foundation.bios import (
    BiosLicenseGate,
    build_external_id_index,
    load_bios_subset_jsonl,
)
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.foundation.ids import (
    EnterpriseKind,
    EvidenceId,
    is_enterprise_id,
    is_evidence_id,
    mint_enterprise_id,
    normalize_evidence_id,
)
from biomed_ontology.foundation.models import AssetHit, EvidenceHit, KnowledgeClaim
from biomed_ontology.foundation.world import load_world_model

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "data" / "foundation"
ONTOLOGY = ROOT / "ontology"


def _backends_ready() -> bool:
    try:
        from pymilvus import MilvusClient

        from biomed_ontology.config import settings
        from biomed_ontology.foundation.catalog import OpenMetadataClient

        if not GraphDbClient.from_settings().health():
            return False
        client = MilvusClient(uri=settings.milvus_uri)
        if not client.has_collection("foundation_evidence"):
            return False
        OpenMetadataClient.from_settings().ping()
        return True
    except Exception:
        return False


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


def test_seed_claims_direction_and_provenance() -> None:
    """YAML seed 约束（入库前校验）；运行时查询不读 YAML。"""
    wm = load_world_model(FOUNDATION)
    claims = [c for c in wm.claims if c.subject_id == "HMD:ENT:DC:savolitinib"]
    predicates = {c.predicate for c in claims}
    assert "testedIn" in predicates
    assert "hasAssay" in predicates
    inverted = [
        c
        for c in wm.claims
        if c.predicate == "supportedBy" and (c.object_id or "").startswith("HMD:ENT:DC:")
    ]
    assert not inverted
    for c in claims:
        assert c.source_type
        assert c.extracted_by


def test_query_rejects_without_graphdb() -> None:
    api = FoundationApi(load_world_model(FOUNDATION))
    api.graphdb = MagicMock()
    api.graphdb.health.return_value = False
    with pytest.raises(BackendUnavailableError, match="GraphDB"):
        api.get_entity("HMD:ENT:DC:savolitinib")
    with pytest.raises(BackendUnavailableError, match="GraphDB"):
        api.get_relationships("HMD:ENT:DC:savolitinib")


def test_golden_path_live_backends() -> None:
    if not _backends_ready():
        pytest.skip(
            "需要 GraphDB + Milvus foundation_evidence + OpenMetadata（先 foundation sync）"
        )
    api = FoundationApi(load_world_model(FOUNDATION))
    result = api.golden_path("HMPL-504")
    assert result["ok"] is True
    assert result["canonical_entity"] == "HMD:ENT:DC:savolitinib"
    ctx = result["context"]
    assert ctx["backends"]["entity"] == "graphdb"
    assert ctx["backends"]["relationships"] == "graphdb"
    assert ctx["backends"]["evidence"] == "milvus"
    assert ctx["backends"]["assets"] == "openmetadata"
    assert str(ctx["backends"].get("bios", "")).startswith("graphdb_biomedical")
    assert "yaml" not in ctx["backends"].values()
    assert ctx.get("bios_bridges")
    assert any(t["id"] == "HMD:ENT:TGT:MET" for t in ctx["targets"])
    assert any(d["id"] == "HMD:ENT:IND:nsclc" for d in ctx["diseases"])
    assert any(e.get("span") for e in ctx["evidence"])
    assert any("exp_2025_012" in (a.get("id") or "") for a in ctx["internal_assets"])
    assert "SELECT " not in str(ctx)


def test_multi_golden_path_eval_live() -> None:
    if not _backends_ready():
        pytest.skip("需要 GraphDB + Milvus + OpenMetadata")
    from biomed_ontology.foundation.golden_eval import eval_golden_paths

    summary = eval_golden_paths()
    assert summary["passed"] == summary["total"], summary["failed"]
    for row in summary["paths"]:
        assert row["checks"]["no_yaml"]
        assert row["checks"]["bios_graphdb"]
        assert row["backends"]["evidence"] == "milvus"
        assert row["backends"]["assets"] == "openmetadata"


def test_observe_retrieval_emits_four_pillars() -> None:
    import io

    from biomed_ontology.foundation import obs_log
    from biomed_ontology.foundation.obs_log import observe_retrieval

    buf = io.StringIO()
    obs_log._CONFIGURED = False
    # 直接把 logger 打到 buffer，避免捕获真实 stderr 句柄
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
    obs_log._CONFIGURED = True
    with observe_retrieval("test.where", op="unit", input_summary={"q": 1}) as st:
        st["backend"] = "milvus"
        st["why"] = {"yaml_fallback": False}
        st["output"] = {"hit_count": 2}
    blob = buf.getvalue()
    for pillar in ("trace", "io", "state", "metrics"):
        assert f'"pillar": "{pillar}"' in blob or f'"pillar":"{pillar}"' in blob
    obs_log._CONFIGURED = False


def test_golden_path_rich_render_with_mock_context() -> None:
    from io import StringIO

    from rich.console import Console

    from biomed_ontology.foundation.render import render_golden_path

    result = {
        "ok": True,
        "path": "DrugCandidate→Target→Disease→Evidence→Asset",
        "canonical_entity": "HMD:ENT:DC:savolitinib",
        "query": "HMPL-504",
        "resolve": {
            "query": "HMPL-504",
            "resolved": [
                {
                    "mention": "HMPL-504",
                    "canonical_entity": "HMD:ENT:DC:savolitinib",
                    "resolution_method": "xref",
                    "confidence": 1.0,
                    "entity_kind": "DrugCandidate",
                    "external_ids": ["BIOS:SAVO_DEMO"],
                }
            ],
        },
        "context": {
            "ontology_release_id": "0.3.0-foundation",
            "enterprise_id": "HMD:ENT:DC:savolitinib",
            "entity": {
                "enterprise_id": "HMD:ENT:DC:savolitinib",
                "entity_kind": "DrugCandidate",
                "preferred_label_en": "savolitinib",
                "preferred_label_zh": "赛沃替尼",
                "aliases": ["HMPL-504"],
                "exact_match_xrefs": ["BIOS:SAVO_DEMO"],
            },
            "targets": [
                {
                    "id": "HMD:ENT:TGT:MET",
                    "type": "Target",
                    "label": "MET",
                    "external_ids": ["HGNC:7029"],
                }
            ],
            "diseases": [
                {
                    "id": "HMD:ENT:IND:nsclc",
                    "type": "Indication",
                    "label": "NSCLC",
                    "external_ids": [],
                }
            ],
            "evidence": [
                {
                    "id": "ev:lit:savo_met_1",
                    "type": "PubMed",
                    "claim": "HMD:ENT:DC:savolitinib targets HMD:ENT:TGT:MET",
                    "span": "selective MET tyrosine kinase inhibitor",
                    "confidence": 0.95,
                }
            ],
            "internal_assets": [
                {
                    "id": "asliva.eln.exp_2025_012",
                    "type": "eln_experiment",
                    "name": "EXP-2025-012",
                }
            ],
            "relationships": [],
            "related_entities": [],
            "backends": {
                "entity": "graphdb",
                "relationships": "graphdb",
                "evidence": "milvus",
                "assets": "openmetadata",
            },
        },
    }
    buf = StringIO()
    cons = Console(file=buf, force_terminal=True, width=100, color_system=None)
    render_golden_path(result, console=cons, verbose=True)
    text = buf.getvalue()
    assert "HMD:ENT:DC:savolitinib" in text
    assert "graphdb" in text
    assert "milvus" in text
    assert "openmetadata" in text


def test_get_entity_context_mocked_stores() -> None:
    """用 mock 三后端验证聚合逻辑，仍不走 YAML。"""
    from biomed_ontology.foundation.models import EnterpriseEntity

    world = load_world_model(FOUNDATION)
    api = FoundationApi(world)
    savo = EnterpriseEntity(
        enterprise_id="HMD:ENT:DC:savolitinib",
        entity_kind="DrugCandidate",
        preferred_label_en="savolitinib",
        targets=["HMD:ENT:TGT:MET"],
        indications=["HMD:ENT:IND:nsclc"],
        exact_match_xrefs=["BIOS:SAVO_DEMO"],
    )
    met = EnterpriseEntity(
        enterprise_id="HMD:ENT:TGT:MET",
        entity_kind="Target",
        preferred_label_en="MET",
        exact_match_xrefs=["HGNC:7029"],
    )
    nsclc = EnterpriseEntity(
        enterprise_id="HMD:ENT:IND:nsclc",
        entity_kind="Indication",
        preferred_label_en="NSCLC",
    )
    claims = [
        KnowledgeClaim(
            claim_id="c1",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate="targets",
            object_id="HMD:ENT:TGT:MET",
            source_id="pubmed:1",
            source_type="literature",
            evidence_ids=["ev:1"],
            span="inhibits MET",
            confidence=0.9,
        )
    ]

    api.graphdb = MagicMock()
    api.graphdb.health.return_value = True

    def _fetch_entity(_c: Any, eid: str) -> EnterpriseEntity | None:
        table = {
            "HMD:ENT:DC:savolitinib": savo,
            "HMD:ENT:TGT:MET": met,
            "HMD:ENT:IND:nsclc": nsclc,
        }
        return table.get(eid)

    with (
        patch("biomed_ontology.foundation.api.fetch_entity", side_effect=_fetch_entity),
        patch("biomed_ontology.foundation.api.fetch_claims", return_value=claims),
        patch(
            "biomed_ontology.foundation.api.fetch_related_ids",
            return_value=["HMD:ENT:TGT:MET", "HMD:ENT:IND:nsclc"],
        ),
        patch(
            "biomed_ontology.foundation.api._search_evidence_milvus",
            return_value=[
                EvidenceHit(
                    evidence_id="ev:1",
                    text="inhibits MET",
                    quote="inhibits MET",
                    entity_ids=["HMD:ENT:DC:savolitinib"],
                    collection="literature",
                    score=0.9,
                )
            ],
        ),
    ):
        api.openmetadata = MagicMock()
        api.openmetadata.ping.return_value = {"version": "1.5"}
        api.openmetadata.search_assets.return_value = [
            AssetHit(
                asset_fqn="asliva.eln.exp_2025_012",
                name="EXP",
                entity_ids=["HMD:ENT:DC:savolitinib"],
                asset_type="eln_experiment",
            )
        ]
        ctx = api.get_entity_context("HMD:ENT:DC:savolitinib")

    assert ctx["backends"]["entity"] == "graphdb"
    assert ctx["backends"]["evidence"] == "milvus"
    assert ctx["backends"]["assets"] == "openmetadata"
    assert ctx["targets"][0]["id"] == "HMD:ENT:TGT:MET"
    assert ctx["internal_assets"][0]["id"] == "asliva.eln.exp_2025_012"


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
    path = ONTOLOGY / "mappings" / "zingg_matches.jsonl"
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines


def test_bios_license_gate_blocks_by_default() -> None:
    gate = BiosLicenseGate()
    with pytest.raises(PermissionError, match="CC-BY-NC-ND"):
        gate.require()
    BiosLicenseGate(acknowledged=True, purpose="poc").require()


def test_bios_load_satisfied_skips_when_marker_and_graph_ready() -> None:
    from biomed_ontology.foundation.bios import _bios_load_satisfied, _marker_rank

    marker = {
        "source": "full_download_concepts_tsv",
        "concepts": 22104562,
        "max_concepts": 0,
    }
    assert _bios_load_satisfied(full=True, marker=marker, max_concepts=0, graph_ready=True)
    assert not _bios_load_satisfied(full=True, marker=marker, max_concepts=0, graph_ready=False)
    # 先前截断、现在要全量 → 不满足
    assert not _bios_load_satisfied(
        full=True,
        marker={**marker, "max_concepts": 1000, "concepts": 1000},
        max_concepts=0,
        graph_ready=True,
    )
    # subset marker 不能满足 full（除非 GraphDB 已有近全量）
    assert not _bios_load_satisfied(
        full=True,
        marker={"source": "subset", "concepts": 50, "max_concepts": 0},
        max_concepts=0,
        graph_ready=True,
    )
    assert _bios_load_satisfied(
        full=True,
        marker={"source": "subset", "concepts": 3, "max_concepts": 0},
        max_concepts=0,
        graph_ready=True,
        graph_count=22_104_562,
    )
    assert _marker_rank(marker) > _marker_rank(
        {"source": "subset", "concepts": 3, "max_concepts": 0}
    )


def test_bios_subset_external_index() -> None:
    concepts = list(load_bios_subset_jsonl(FOUNDATION / "bios_subset.jsonl"))
    idx = build_external_id_index(concepts)
    assert "BIOS:MET_DEMO" in idx.lookup_external("HGNC:7029")
    assert "BIOS:SAVO_DEMO" in idx.lookup_external("DrugBank:DEMO_SAVO")


def test_enterprise_id_from_iri_roundtrip() -> None:
    from biomed_ontology.foundation.store import enterprise_id_from_iri
    from biomed_ontology.foundation.world import entity_iri

    eid = "HMD:ENT:DC:savolitinib"
    assert enterprise_id_from_iri(entity_iri(eid)) == eid


def test_foundation_mcp_exposes_get_entity_context() -> None:
    import asyncio

    from biomed_ontology.service.deps import build_state, set_state
    from biomed_ontology.service.mcp import create_mcp
    from biomed_ontology.tools import TOOL_SPECS

    set_state(build_state())
    try:
        tools = asyncio.run(create_mcp().list_tools())
        names = {t.name for t in tools}
        assert "get_entity_context" in names
        assert "resolve_entity" in names
        assert {s["name"] for s in TOOL_SPECS} <= names
        assert "graph_sparql" not in names
        assert "vector_search" not in names
    finally:
        set_state(None)
