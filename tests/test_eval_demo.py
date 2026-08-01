"""gold set 评测与 6 个演示场景。

评测用例守的是"数字还在"，demo 用例守的是"故事还成立"——
两者都会因为下游改动而静默失效，所以都得进 CI。
"""

from __future__ import annotations

import pytest

from biomed_ontology.demo import DEMOS, run_all
from biomed_ontology.eval import ARMS, eval_normalization, eval_retrieval

LICENSED = frozenset({"MOCK_LICENSED"})


def test_normalization_meets_the_accuracy_floor(kb):
    ev = eval_normalization(kb)
    assert ev.accuracy >= 0.90, ev.as_table()


def test_accuracy_is_reported_per_entity_type(kb):
    """总体准确率会把某一类的塌陷平均掉。"""
    by_type = eval_normalization(kb).accuracy_by_type()
    assert set(by_type) >= {"SUBSTANCE", "TARGET", "DISEASE"}
    assert all(v >= 0.85 for v in by_type.values()), by_type


def test_gold_set_contains_negative_cases(kb):
    """只测正例的 gold set 会奖励"什么都往上猜"的实现。"""
    from biomed_ontology.eval import load_gold

    gold = load_gold("normalization")
    assert any(c.get("expect") is None for c in gold["cases"])


def test_retrieval_arms_are_all_evaluated(kb):
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert set(ev.arms) == set(ARMS)


def test_ontology_hybrid_improves_recall_over_bm25(kb):
    """本体增强的核心承诺就是召回 —— 这条掉了整个方案的价值主张就没了。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0, ev.as_table()


def test_expansion_trades_top1_precision_for_recall(kb):
    """扩展提召回、摊薄 top-1，这个权衡必须看得见。

    只报 Recall 会把"排序变差了"藏起来；写成断言后，哪天 rerank 把 MRR 拉回来，
    这条测试会失败并提醒把结论改掉 —— 那正是它应该发生的时候。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0
    assert ev.lift("mrr") <= 0, "MRR 不再下降了，请同步更新对外结论"


def test_entitlement_gated_queries_are_skipped_without_the_entitlement(kb):
    """无凭据时跳过商业源的查询，而不是当成"没召回"计零分。

    算成零分会让"没买数据"和"检索做得差"混成同一个数字，
    于是采购决策拿不到任何有效信号。
    """
    free = eval_retrieval(kb, entitlements=frozenset())
    paid = eval_retrieval(kb, entitlements=LICENSED)
    n_free = len(free.arms[free.baseline].per_query)
    n_paid = len(paid.arms[paid.baseline].per_query)
    assert n_free < n_paid


# ------------------------------------------------------------------ demo


@pytest.mark.parametrize("demo_id", sorted(DEMOS))
def test_demo_passes(kb, demo_id):
    from biomed_ontology.agentapi import AgentApi
    from biomed_ontology.demo import run_demo

    result = run_demo(demo_id, kb, AgentApi.from_kb(kb))
    assert result.passed, result.render()


def test_all_demos_pass_together(kb):
    results = run_all(kb)
    assert all(r.passed for r in results)
    assert len(results) == len(DEMOS)


def test_every_demo_states_a_falsifiable_claim(kb):
    """demo 必须自带断言。

    只打印一段好看的输出、不校验任何东西的"演示"，
    在下游改坏之后依然会打印那段好看的输出。
    """
    for result in run_all(kb):
        assert result.claim
        assert result.lines
