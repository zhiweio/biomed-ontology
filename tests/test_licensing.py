"""许可分层与数据源注册表（D10）。

许可判断错误 = 合规事故，因此这些断言优先级等同于安全测试。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import EntityTypeEnum, LicenseTierEnum
from biomed_ontology.licensing import (
    LicenseViolation,
    assert_exportable,
    is_exportable,
    is_trainable,
    named_graph_uri,
    policy_for,
)
from biomed_ontology.registry import SourceRole, Track


def test_every_source_declares_a_tier(registry):
    for s in registry:
        assert isinstance(s.license_tier, LicenseTierEnum)
        assert s.license_id, f"{s.id} 缺少 license_id"


def test_source_ids_are_unique(registry):
    ids = [s.id for s in registry]
    assert len(ids) == len(set(ids))


def test_unknown_source_raises(registry):
    with pytest.raises(KeyError, match="未注册"):
        registry["NOT_A_SOURCE"]


@pytest.mark.parametrize("tier", [LicenseTierEnum.TIER_2, LicenseTierEnum.TIER_3])
def test_restricted_tiers_are_not_exportable(tier: LicenseTierEnum):
    assert not is_exportable(tier)
    with pytest.raises(LicenseViolation):
        assert_exportable(tier, what="test payload")


@pytest.mark.parametrize(
    "tier",
    [LicenseTierEnum.TIER_1, LicenseTierEnum.TIER_2, LicenseTierEnum.TIER_3],
)
def test_only_tier_0_is_trainable(tier: LicenseTierEnum):
    """share-alike 与订阅内容一律不得进入训练语料。"""
    assert not is_trainable(tier)


def test_tier_0_passes_export_gate():
    assert_exportable(LicenseTierEnum.TIER_0, what="open data")


def test_share_alike_flagged_on_tier_1():
    """CC-BY-SA 的传染性必须显式建模，否则衍生数据的分发条件会被悄悄改变。"""
    assert policy_for(LicenseTierEnum.TIER_1).share_alike


def test_named_graph_encodes_tier():
    """tier 编进图 URI，SPARQL 才能用 FROM NAMED 做集合级过滤。"""
    uri = named_graph_uri("MEDDRA", LicenseTierEnum.TIER_3)
    assert uri.endswith("/tier_3/meddra")


def test_named_graphs_are_unique_per_source(registry):
    graphs = [s.named_graph for s in registry]
    assert len(graphs) == len(set(graphs))


# ---------------------------------------------------------------- 可见性过滤


def test_restricted_sources_hidden_without_entitlement(registry):
    visible = {s.id for s in registry.visible_to(frozenset())}
    for s in registry.active():
        if s.license_tier in (LicenseTierEnum.TIER_2, LicenseTierEnum.TIER_3):
            assert s.id not in visible, f"{s.id} 在无凭据时不应可见"


def test_entitlement_unlocks_matching_source(registry):
    """许可插槽验证：MOCK_LICENSED 是 TIER_3 且已启用，用于跑通完整链路。"""
    mock = registry["MOCK_LICENSED"]
    assert mock.license_tier is LicenseTierEnum.TIER_3
    assert mock.enabled

    without = {s.id for s in registry.visible_to(frozenset())}
    with_ent = {s.id for s in registry.visible_to(frozenset({"MOCK_LICENSED"}))}
    assert "MOCK_LICENSED" not in without
    assert "MOCK_LICENSED" in with_ent


def test_entitlement_does_not_leak_to_other_sources(registry):
    """持有 A 源凭据不得解锁 B 源。"""
    visible = {s.id for s in registry.visible_to(frozenset({"MOCK_LICENSED"}))}
    for s in registry.active():
        if s.license_tier is LicenseTierEnum.TIER_3 and s.id != "MOCK_LICENSED":
            assert s.id not in visible


# ---------------------------------------------------------------- 双轨策略


def test_track_b_sources_are_slots_not_active(registry):
    """采购未到位前 Track B 必须全部停用，MOCK_LICENSED 除外（它是插槽验证用）。"""
    for s in registry.by_track(Track.B):
        if s.id == "MOCK_LICENSED":
            continue
        assert not s.enabled, f"{s.id} 尚未采购，不应启用"
        assert s.requires_credentials


def test_procurement_slots_ordered_by_priority(registry):
    slots = registry.procurement_slots()
    priorities = [s.procurement_priority for s in slots]
    assert priorities == sorted(priorities)
    assert slots[0].id == "UMLS", "UMLS 性价比最高，应排首位"


def test_poc_runs_entirely_on_track_a(registry):
    """PoC 不得依赖任何采购数据 —— 否则方法论验证会被采购周期阻塞。"""
    active_b = [s for s in registry.active() if s.track is Track.B]
    assert {s.id for s in active_b} == {"MOCK_LICENSED"}


def test_chembl_is_demoted_to_validation(registry):
    """ChEMBL 是 CC-BY-SA，做主干会把 share-alike 传染给整个衍生数据集。"""
    chembl = registry["CHEMBL"]
    assert chembl.role is SourceRole.VALIDATION
    assert chembl.license_tier is LicenseTierEnum.TIER_1

    substance_authorities = registry.authoritative_for(EntityTypeEnum.SUBSTANCE)
    assert "UNII" in {s.id for s in substance_authorities}
    assert all(s.license_tier is LicenseTierEnum.TIER_0 for s in substance_authorities)


def test_every_entity_type_has_an_authoritative_source(registry):
    """ADVERSE_EVENT 例外 —— 它依赖 MedDRA 采购，PoC 阶段无权威源是预期状态。"""
    for et in EntityTypeEnum:
        if et is EntityTypeEnum.ADVERSE_EVENT:
            continue
        if not registry.by_entity_type(et):
            continue
        assert registry.authoritative_for(et), f"{et.value} 缺少权威源"
