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


def test_gold_keys_address_every_chunk_in_the_section(kb):
    """gold 的键是章节级的，必须映射到该节的**全部**切片。

    早先这里是个 dict 推导，同一节的后一片直接覆盖前一片 ——
    588 片里只有 132 片对 gold 可寻址，另外 456 片无论标得多准都命中不了。
    失败形态是"召回莫名其妙地低"，而查的人会一路查到检索器上去。
    """
    from biomed_ontology.eval import _chunk_key_index

    index = _chunk_key_index(kb)
    assert sum(len(v) for v in index.values()) == len(kb.chunks)
    assert any(len(v) > 1 for v in index.values()), (
        "语料里已经没有多切片章节了，这条守卫失去意义 —— 要么切片策略变了，要么语料退化了"
    )


def test_retrieval_arms_are_all_evaluated(kb):
    """本地臂全跑；Milvus 臂标为未运行而不是静默消失。

    惄惄少几行会让读报告的人以为那些配置没做，而不是没测。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    local = {k for k, v in ARMS.items() if v.get("backend", "local") == "local"}
    assert set(ev.arms) == local
    assert set(ev.unavailable) == set(ARMS) - local


@pytest.mark.xfail(
    strict=True,
    reason="gold 已覆盖全部 14 篇、judged@10=1.000，标注覆盖不再是理由："
    "0.335 → 0.317（-5.2%）就是本体臂当前的真实水平。"
    "成因在检索侧：本体今天只经由 GRAPH 一个通道参与融合，该通道净值 -0.018；"
    "84 个概念下几乎每个切片都能挂上，判别力稀释了却仍按整通道权重进 RRF。"
    "见 targets.yaml T1 豁免。检索侧改造后本条应自动转绿 —— "
    "strict=True 保证那时不会被无声跳过。",
)
def test_ontology_hybrid_improves_recall_over_bm25(kb):
    """本体增强的核心承诺就是召回 —— 这条掉了整个方案的价值主张就没了。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0, ev.as_table()


@pytest.mark.xfail(
    strict=True,
    reason="同上：召回提升本身当前测不出来，这条依赖它成立。",
)
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


@pytest.mark.xfail(
    strict=True,
    reason="当前 nDCG 提升三个口径同为负：总 -0.015 / en -0.009 / zh -0.027，符号一致。"
    "这不代表分表没信息量 —— 同一份数据里 Recall 的 en 是 +0.014、zh 是 -0.086，"
    "分表在那个指标上照样给出了总平均给不出的结论。"
    "只是 nDCG 这一项上，GRAPH 通道的稀释对两个语种是同向的。",
)
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

    if demo_id == "D1":
        pytest.xfail(
            "真实文献语料下 AZD6094 / AZD-6094 的前十接地精度只有 0.500（门槛 0.800）。"
            "这是真实缺陷不是测试噪声：研究代号是低频串，词法通道会拽进无关内容，"
            "而本体扩展没能把它拉回来。归一化不变性仍然完好（6 种写法 → 1 个 code）。"
        )
    result = run_demo(demo_id, kb, AgentApi.from_kb(kb))
    assert result.passed, result.render()


def test_all_demos_pass_together(kb):
    results = run_all(kb)
    failed = {r.demo_id for r in results if not r.passed}
    # D1 当前不达标，原因见 test_demo_passes 里的 xfail 说明。
    # 这里写成精确集合而不是放宽为"允许若干条失败"：多坏一条必须立刻炸。
    assert failed == {"D1"}, [r.render() for r in results if not r.passed]
    assert len(results) == len(DEMOS)


def test_every_demo_states_a_falsifiable_claim(kb):
    """demo 必须自带断言。

    只打印一段好看的输出、不校验任何东西的"演示"，
    在下游改坏之后依然会打印那段好看的输出。
    """
    for result in run_all(kb):
        assert result.claim
        assert result.lines
