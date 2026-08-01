"""质量守门与演进闭环。

守门的价值全在"该拦的时候真的拦住"，所以下面多数用例是构造违规后验证被拦。
"""

from __future__ import annotations

import dataclasses as dc

import pytest

from biomed_ontology._generated.hmd_obs import SignalStatusEnum, SignalTypeEnum
from biomed_ontology.evolution import (
    MiningInput,
    build_changeset,
    generate_candidates,
    mine_signals,
    plan_release,
    write_release_artifacts,
)
from biomed_ontology.quality import (
    ACCURACY_FLOOR,
    QualityGate,
    check_consistency,
    stratified_sample,
)

ACC = {"SUBSTANCE": 0.94, "TARGET": 0.94, "DISEASE": 0.94}
SHAPES = "schema/shapes/projection.shacl.ttl"


# ------------------------------------------------------------------ 一致性


def test_seed_knowledge_base_is_consistent(kb):
    assert check_consistency(kb) == []


def test_dangling_parent_is_caught(kb):
    concepts = list(kb.concepts)
    concepts[0] = dc.replace(concepts[0], parents=["HMD:DIS:9999999"])
    broken = dc.replace(kb, concepts=concepts)
    assert any("dangling_parent" in v.rule for v in check_consistency(broken))


def test_hierarchy_cycle_is_caught(kb):
    """环会让层级扩展无限递归 —— 而扩展是检索的默认行为。"""
    concepts = [
        dc.replace(c, parents=["HMD:DIS:0000001"]) if c.concept_id == "HMD:DIS:0000003" else c
        for c in kb.concepts
    ]
    broken = dc.replace(kb, concepts=concepts)
    assert any("cycle" in v.rule for v in check_consistency(broken))


def test_stratified_sample_covers_every_stratum(kb):
    """抽检必须分层。

    随机抽样会让图片模态这类小样本层长期抽不到，
    于是它的错误率永远不进指标 —— 直到有人在汇报里用了那条事实。
    """
    plan = stratified_sample(kb)
    assert plan.strata
    assert any(k.startswith("modality:") for k in plan.strata)
    assert any(k.startswith("entity:") for k in plan.strata)


def test_sampling_is_reproducible(kb):
    """同一种子抽出同一批样本，否则两次抽检的准确率不可比。"""
    assert stratified_sample(kb).strata == stratified_sample(kb).strata


def test_gate_passes_on_clean_kb(kb):
    assert QualityGate().evaluate(kb, manual_accuracy=ACC).passed


def test_gate_blocks_when_accuracy_below_floor(kb):
    low = {**ACC, "SUBSTANCE": ACCURACY_FLOOR - 0.05}
    result = QualityGate().evaluate(kb, manual_accuracy=low)
    assert not result.passed
    assert result.blocking


def test_gate_blocks_on_regression_even_when_above_floor(kb):
    """高于底线但相对上版明显退步，同样要拦。

    只看绝对底线的话，质量可以一版掉一点，每版都"合格"，
    半年后回头看已经不能用了。
    """
    previous = dict.fromkeys(ACC, 0.99)
    now = QualityGate().evaluate(kb, manual_accuracy=dict.fromkeys(ACC, 0.93), previous=previous)
    assert not now.passed
    assert any("regression" in b for b in now.blocking)


# ------------------------------------------------------------------ 演进闭环


@pytest.fixture(scope="module")
def signals(kb):
    """先制造真实使用痕迹，再挖信号 —— 没有使用就不该有信号。"""
    from biomed_ontology.agentapi import AgentApi
    from biomed_ontology.demo import run_all

    api = AgentApi.from_kb(kb)
    run_all(kb, api)
    return mine_signals(MiningInput.from_runtime(kb, api)), api


def test_signals_come_from_real_usage(signals):
    sigs, _ = signals
    assert sigs
    assert all(s.occurrences >= 1 for s in sigs)


def test_signal_ids_are_stable_across_reruns(kb, signals):
    """重复挖掘不能产生新 ID。

    ID 一漂移，"这条我已经处置过了"就无从表达，
    curator 每轮都会看到同一批已驳回的候选。
    """
    sigs, api = signals
    again = mine_signals(MiningInput.from_runtime(kb, api))
    assert {s.signal_id for s in again} == {s.signal_id for s in sigs}


def test_new_signals_start_unprocessed(signals):
    sigs, _ = signals
    assert all(s.status is SignalStatusEnum.NEW for s in sigs)


def test_abstention_becomes_an_unmapped_signal(signals):
    """弃权不是终点，是本体该长出新概念的入口。"""
    sigs, _ = signals
    assert any(s.signal_type is SignalTypeEnum.unmapped_span for s in sigs)


def test_candidates_never_include_destructive_operations(kb, signals):
    """自动生成只做加法。

    合并与废弃会让既有 ID 失效，而 ID 是对外契约的一部分 ——
    这类操作必须有人签字，不能由挖掘结果自动触发。
    """
    sigs, _ = signals
    ops = generate_candidates(kb, sigs)
    kinds = {op.op for op in ops}
    assert not (kinds & {"merge", "obsolete", "delete"})


def test_every_candidate_traces_back_to_a_signal(kb, signals):
    """没有信号出处的变更建议，curator 无从判断该不该接受。"""
    sigs, _ = signals
    known = {s.signal_id for s in sigs}
    for op in generate_candidates(kb, sigs):
        assert op.signal_id in known
        assert op.rationale


def test_changeset_serialises_to_kgcl(kb, signals):
    sigs, _ = signals
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    text = cs.to_kgcl()
    assert text.strip()
    assert all(
        line.startswith("#") or line.strip() == "" or " " in line for line in text.splitlines()
    )


def test_release_is_blocked_without_human_approval(kb, signals):
    sigs, _ = signals
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    gate = QualityGate().evaluate(kb, manual_accuracy=ACC)
    plan = plan_release(kb, cs, gate_result=gate, approved_by=None)
    assert not plan.approved
    assert "人工" in plan.explain() or "approval" in plan.explain().lower()


def test_release_proceeds_once_approved(kb, signals, tmp_path):
    sigs, _ = signals
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    gate = QualityGate().evaluate(kb, manual_accuracy=ACC)
    plan = plan_release(kb, cs, gate_result=gate, approved_by="curator@asliva")
    assert plan.approved
    written = write_release_artifacts(plan, tmp_path)
    assert written
    assert all(p.exists() for p in written)


def test_release_is_blocked_when_quality_gate_fails(kb, signals):
    """质量不过关时，演进本身也不该放行 —— 否则闭环会把问题放大一轮。"""
    sigs, _ = signals
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    bad = QualityGate().evaluate(kb, manual_accuracy={k: 0.5 for k in ACC})
    plan = plan_release(kb, cs, gate_result=bad, approved_by="curator@asliva")
    assert not plan.approved


def test_impact_analysis_reports_reindex_scope(kb, signals):
    """改本体要知道会影响多少切片，否则"要不要重建索引"只能靠猜。"""
    sigs, _ = signals
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    gate = QualityGate().evaluate(kb, manual_accuracy=ACC)
    plan = plan_release(kb, cs, gate_result=gate, approved_by="c")
    assert plan.impact["chunks_affected"] >= 0
    assert "reindex_required" in plan.impact
