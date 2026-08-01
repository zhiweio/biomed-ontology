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
    """本地臂全跑；Milvus 臂标为未运行而不是静默消失。

    惄惄少几行会让读报告的人以为那些配置没做，而不是没测。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    local = {k for k, v in ARMS.items() if v.get("backend", "local") == "local"}
    assert set(ev.arms) == local
    assert set(ev.unavailable) == set(ARMS) - local


def test_ontology_hybrid_improves_recall_over_bm25(kb):
    """本体增强的核心承诺就是召回 —— 这条掉了整个方案的价值主张就没了。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0, ev.as_table()


def test_expansion_trades_top1_precision_for_recall(kb):
    """扩展提召回、摊薄 top-1，这个权衡必须看得见。

    这条早先是个写死的 `lift("mrr") <= 0` 断言。现已改由 T4 目标承载 ——
    写死的断言只能表达"我预期它很差"，表达不了"我希望它好、当前没做到、原因如下"。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0
    assert ev.lift("map_score") >= 0, "MAP 也降了，那就不是首位抖动而是真的排序退化"


def test_metrics_are_reported_per_language(kb):
    """SapBERT 是英文单语模型，中文语料上大概率无增益甚至有害。

    只报总平均会把"英文涨了、中文没动"抹平成一个好看的数字，
    而按语种路由向量列这个决定，恰恰只能从分语种的表里读出来。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    for arm in ev.arms.values():
        assert set(arm.by_lang) >= {"en", "zh"}
        assert sum(sub.query_count for sub in arm.by_lang.values()) == arm.query_count


def test_language_split_can_disagree_with_the_average(kb):
    """分语种表必须真的能和总平均给出不同结论，否则拆分只是装饰。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    overall = ev.lift("ndcg_at_10")
    per_lang = [ev.lift("ndcg_at_10", lang=lg) for lg in ("en", "zh")]
    assert any((x > 0) != (overall > 0) for x in per_lang), (
        "当前数据上分语种与总平均结论一致；若长期如此需重新确认分表是否还有信息量"
    )


def test_unavailable_arms_are_named_not_omitted(kb):
    """没跑的臂要写出来。悄悄少几行会让人以为那些配置没做，而不是没测。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert "milvus_hybrid_3col" in ev.unavailable
    assert "未运行的臂" in ev.as_table()


def test_sapbert_delta_discloses_which_embedder_produced_it():
    """ "SapBERT 净值"这个标题本身会误导 —— fake 嵌入器下那一列根本不是 SapBERT。

    数字和它的产地必须同屏，否则会被当成模型结论转述进采购文档。
    """
    from biomed_ontology.eval import ArmResult, RetrievalEval

    def arm(name: str, recall: float) -> ArmResult:
        return ArmResult(
            arm=name, label=name, recall_at_10=recall, precision_at_5=0.0, ndcg_at_10=0.0, mrr=0.0
        )

    arms = {
        "milvus_hybrid_3col": arm("milvus_hybrid_3col", 0.8),
        "milvus_hybrid_2col": arm("milvus_hybrid_2col", 0.9),
        "bm25_only": arm("bm25_only", 0.5),
        "ontology_hybrid": arm("ontology_hybrid", 0.6),
    }
    faked = RetrievalEval(arms=arms, embedder="fake").as_table()
    assert "embedder=fake" in faked
    assert "并未加载 SapBERT" in faked

    real = RetrievalEval(arms=arms, embedder="dual").as_table()
    assert "embedder=dual" in real
    assert "并未加载 SapBERT" not in real


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
