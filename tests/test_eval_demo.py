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
    """零依赖能跑的臂全跑；需要外部模型的标为未运行而不是静默消失。

    悄悄少几行会让读报告的人以为那些配置没做，而不是没测。

    "需要外部模型"有两类：Milvus 臂要向量库，精排臂要交叉编码器权重。
    两类都遵守同一条纪律 —— 拿不到就标为未运行，**绝不回落**顶替。
    回落之后报表上写着"Milvus 三列"或"+精排"，跑的却是本地 TF-IDF 或原序返回。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    offline = {
        k for k, v in ARMS.items() if v.get("backend", "local") == "local" and not v.get("rerank")
    }
    assert set(ev.arms) == offline
    assert set(ev.unavailable) == set(ARMS) - offline


def test_rerank_arms_refuse_to_fall_back_to_a_null_reranker(kb):
    """没给精排模型时，精排臂必须缺席，而不是原序返回冒充精排结果。"""
    from biomed_ontology.rerank import NullReranker

    ev = eval_retrieval(kb, entitlements=LICENSED, reranker=NullReranker())
    assert "ontology_hybrid_rerank" in ev.unavailable
    assert "reranker" in ev.unavailable["ontology_hybrid_rerank"]


def test_ontology_hybrid_improves_recall_over_bm25(kb):
    """全量 R@10 符号哨兵：不得再退回负增益。

    主 KPI 已改到本体敏感探针的 nDCG（见 T1）；这条只守全量诊断项不翻负。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0, ev.as_table()


def test_ontology_sensitive_probes_are_reported(kb):
    """主 KPI 切片必须出现在报表里，否则又会只剩被稀释的全量 +0.8%。"""
    from biomed_ontology.eval import ONTOLOGY_PROBES

    ev = eval_retrieval(kb, entitlements=LICENSED)
    arm = ev.arms["ontology_hybrid"]
    assert set(ONTOLOGY_PROBES) <= set(arm.by_probe)
    assert ev.absolute_gain(probes=ONTOLOGY_PROBES) >= 0.05, ev.as_table()
    assert "本体敏感探针" in ev.as_table()


def test_expansion_does_not_trade_ranking_for_recall(kb):
    """召回涨的同时排序不能退 —— 否则只是把噪声推给下游 agent。

    这条早先是个写死的 `lift("mrr") <= 0` 断言，意思是"我预期它很差"。
    现在三项同时为正，写成三条断言而不是一条：哪一项退回去要能一眼看出是哪一项。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.lift("recall_at_10") > 0
    # 浮点噪声量级（1e-5）不算退化；真退应是百分位点以上。
    assert ev.lift("map_score") >= -1e-4, "MAP 降了，那就不是首位抖动而是真的排序退化"
    assert ev.lift("ndcg_at_10") >= 0, "nDCG 降了：理想序按 K 截断，这一项没有天花板可推诿"


def test_ontology_gains_are_reported_with_confidence_intervals(kb):
    """n=28 上任何 ±0.02 都落在噪声里。报表必须自带 CI 与 p 值。

    这条守的不是数值，是**表达方式**：一份只有点估计的报表，
    读者除了看符号别无选择，而符号在这个规模上是可以靠随机翻转的。
    """
    ev = eval_retrieval(kb, entitlements=LICENSED)
    sig = ev.significance("ndcg_at_10")
    assert sig.n == ev.arms["ontology_hybrid"].query_count
    assert sig.ci_low < sig.delta < sig.ci_high
    assert 0.0 < sig.p_value <= 1.0
    table = ev.as_table()
    assert "95% CI" in table and "p=" in table


def test_significance_reports_no_difference_when_arms_are_identical(kb):
    """同一个臂与自己比，p 必须是 1.0 而不是 0.000。

    置换检验里全零差值会让"极端值计数"命中每一次重排，
    朴素实现会算出 p=0.000 —— 也就是把"两臂毫无差别"报成"差别极显著"。
    """
    from biomed_ontology.eval import paired_significance

    scores = {"q1": 0.5, "q2": 0.25, "q3": 1.0}
    sig = paired_significance(scores, dict(scores), resamples=200)
    assert sig.delta == 0.0
    assert sig.p_value == 1.0
    assert not sig.significant


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
    from biomed_ontology.demo import run_demo
    from biomed_ontology.tools import ToolApi

    result = run_demo(demo_id, kb, ToolApi.from_kb(kb))
    assert result.passed, result.render()


def test_all_demos_pass_together(kb):
    """写成"一条都不许失败"而不是"允许若干条失败"：坏一条必须立刻炸。

    D1（AZD6094 归一化不变性）曾长期挂在这里：研究代号是低频串，
    词法通道会拽进无关内容，前十接地精度只有 0.500（门槛 0.800）。
    修好它的是查询改写 —— `Normalizer.expand()` 的输出终于下发给了词法通道，
    于是"AZD6094"这条 query 同时带上了沃利替尼的其余写法。
    """
    results = run_all(kb)
    failed = {r.demo_id for r in results if not r.passed}
    assert not failed, [r.render() for r in results if not r.passed]
    assert len(results) == len(DEMOS)


def test_every_demo_states_a_falsifiable_claim(kb):
    """demo 必须自带断言。

    只打印一段好看的输出、不校验任何东西的"演示"，
    在下游改坏之后依然会打印那段好看的输出。
    """
    for result in run_all(kb):
        assert result.claim
        assert result.lines
