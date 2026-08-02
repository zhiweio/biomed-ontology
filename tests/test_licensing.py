"""许可分层与数据源注册表（D10）。

许可判断错误 = 合规事故，因此这些断言优先级等同于安全测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology._generated.hmd_concept import EntityTypeEnum, LicenseTierEnum
from biomed_ontology.licensing import (
    COMPONENTS,
    LicenseViolation,
    assert_component_cleared,
    assert_exportable,
    is_exportable,
    is_trainable,
    named_graph_uri,
    policy_for,
    uncleared_components,
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
    ranked = [s.procurement_priority for s in slots if s.procurement_priority is not None]
    assert ranked == sorted(ranked)
    assert slots[0].id == "UMLS", "UMLS 性价比最高，应排首位"


def test_unpriced_slots_sort_after_the_priced_ones(registry):
    """不是每个插槽都有价签。

    CLINICAL_IMAGING 卡在 DUA 与伦理审批上，不是卡在预算上，
    给它编一个优先级就等于谎称它和 UMLS 在同一张比价表上。
    留空是有意的，代价是排序键必须容得下 None —— 这里就是那道绊线。
    """
    slots = registry.procurement_slots()
    unpriced = [i for i, s in enumerate(slots) if s.procurement_priority is None]
    priced = [i for i, s in enumerate(slots) if s.procurement_priority is not None]
    assert unpriced, "没有无价签插槽时本测试无意义，删掉它而不是留着空跑"
    assert min(unpriced) > max(priced), "无价签插槽必须排在有价签的之后"


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


# ------------------------------------------------------- 第三方软件组件义务


def test_uncleared_component_blocks_activation():
    """法务义务只写进 NOTICE 就只有写它的人知道；写成闸门才绕不过去。"""
    with pytest.raises(LicenseViolation, match="尚未经法务结论"):
        assert_component_cleared("mineru")


def test_cleared_component_passes():
    assert_component_cleared("knowhere")  # 不抛即通过


def test_unknown_component_is_not_gated():
    """未登记的组件不拦 —— 闸门管的是已识别义务，不是白名单。"""
    assert_component_cleared("pytest")


def test_explicit_acknowledgement_allows_local_trial():
    assert_component_cleared("mineru", accept_uncleared=True)


def test_pending_components_are_enumerable_for_legal_review():
    ids = {c.component_id for c in uncleared_components()}
    assert {"mineru", "pymupdf"} <= ids
    for c in uncleared_components():
        assert c.obligation, f"{c.component_id} 登记了待核实却没写义务内容"


def test_permissive_weights_do_not_clear_a_use_restricted_model():
    """BiomedCLIP 的权重是 MIT —— 依赖清单上它是全场最干净的一条。

    风险不在许可证里：模型卡另有一句"任何部署用途当前均超出适用范围"。
    只读 license_id 会得出"MIT，放行"，所以闸门必须拦住它。
    """
    with pytest.raises(LicenseViolation, match="尚未经法务结论"):
        assert_component_cleared("biomedclip")

    ob = COMPONENTS["biomedclip"]
    assert "MIT" in ob.license_id, "许可证本身要如实记，不能为了触发闸门谎报成受限许可"
    assert "超出适用范围" in ob.obligation, "拦住它的那句话必须写在义务里，否则法务无从复核"


def test_every_pending_obligation_also_appears_in_notice():
    """闸门和 NOTICE 是同一件事的两面，缺一面就漏一类读者。

    只进闸门：分发方拿不到义务正文，NOTICE 就是不完整的。
    只进 NOTICE：跑代码的人绕过去也没人拦。
    两边都要有，而"两边都要有"这件事本身也得有人守着。
    """
    notice = (Path(__file__).resolve().parents[1] / "NOTICE").read_text(encoding="utf-8")
    for c in uncleared_components():
        assert c.component_id in notice, (
            f"{c.component_id} 登记为待核实却没写进 NOTICE —— 分发时义务不可见"
        )


def test_clinical_use_prohibition_is_recorded_not_just_the_copyright_terms():
    """模型卡同时声明未经临床验证。

    这一条和版权无关，却是医学事务唯一会看的那一条 ——
    丢了它，义务正文就只剩法务视角。
    """
    assert "临床" in COMPONENTS["biomedclip"].obligation
