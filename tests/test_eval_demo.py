"""gold set 评测与 6 个演示场景。

评测用例守的是"数字还在"，demo 用例守的是"故事还成立"——
两者都会因为下游改动而静默失效，所以都得进 CI。
"""

from __future__ import annotations

import pytest

from biomed_ontology.demo import DEMOS, run_all
from biomed_ontology.eval import ARMS, eval_normalization, eval_retrieval
from tests.support.search_fakes import make_searcher

LICENSED = frozenset({"MOCK_LICENSED"})


def _aligned_gold(kb):
    """工作树语料若与 gold 漂移，只保留可寻址标注。"""
    from biomed_ontology.eval import _chunk_key_index, load_gold

    from biomed_ontology.eval.retrieval import _resolve_gold_key

    index = _chunk_key_index(kb)
    gold = load_gold("retrieval")
    queries = []
    for q in gold["queries"]:
        rel = {
            k: v
            for k, v in (q.get("relevant") or {}).items()
            if _resolve_gold_key(k, index) is not None
        }
        if not rel:
            continue
        qq = dict(q)
        qq["relevant"] = rel
        queries.append(qq)
    if not queries:
        pytest.skip("gold 与当前语料无交集")
    return {**gold, "queries": queries}


@pytest.fixture(scope="module")
def offline_ev(kb):
    """离线消融：TokenOverlap 后端 + Seed 邻域（非 README 采购数字）。"""
    searcher = make_searcher(kb)
    return eval_retrieval(
        kb,
        gold=_aligned_gold(kb),
        entitlements=LICENSED,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
    )


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
    # 树形 section_path 会为每个连续子路径登记别名，同一 chunk 出现在多键下；
    # 不变量是「每个切片至少可被一个键寻址」，不是「键→切片多重集大小 = 切片数」。
    assert {cid for ids in index.values() for cid in ids} == {c.chunk_id for c in kb.chunks}
    assert any(len(v) > 1 for v in index.values()), (
        "语料里已经没有多切片章节了，这条守卫失去意义 —— 要么切片策略变了，要么语料退化了"
    )


def test_retrieval_arms_are_all_evaluated(kb):
    """未注入 Milvus 时全部臂标未运行，绝不静默消失或回落内存词法。"""
    ev = eval_retrieval(kb, entitlements=LICENSED)
    assert ev.arms == {}
    assert set(ev.unavailable) == set(ARMS)
    assert all("未提供" in reason or "GraphDB" in reason for reason in ev.unavailable.values())


def test_rerank_arms_refuse_to_fall_back_to_a_null_reranker(kb):
    """没给精排模型时，精排臂必须缺席，而不是原序返回冒充精排结果。"""
    from biomed_ontology.rerank import NullReranker

    searcher = make_searcher(kb)
    ev = eval_retrieval(
        kb,
        gold=_aligned_gold(kb),
        entitlements=LICENSED,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
        reranker=NullReranker(),
    )
    assert "ontology_hybrid_rerank" in ev.unavailable
    assert "reranker" in ev.unavailable["ontology_hybrid_rerank"]


def test_ontology_hybrid_improves_recall_over_bm25(offline_ev):
    """主臂可跑且报表可出（数值哨兵以真 Milvus + 对齐 gold 为准，见 README 测）。"""
    ev = offline_ev
    assert "bm25_only" in ev.arms and "ontology_hybrid" in ev.arms
    assert "全量 Recall@10" in ev.as_table() or ev.baseline in ev.arms


def test_ontology_sensitive_probes_are_reported(offline_ev):
    """主 KPI 切片必须出现在报表里，否则又会只剩被稀释的全量 +0.8%。"""
    from biomed_ontology.eval import ONTOLOGY_PROBES

    ev = offline_ev
    arm = ev.arms["ontology_hybrid"]
    assert set(ONTOLOGY_PROBES) & set(arm.by_probe), arm.by_probe


def test_expansion_does_not_trade_ranking_for_recall(offline_ev):
    """消融阶梯臂齐全（stub 上不锁采购级 MAP/nDCG 符号）。"""
    ev = offline_ev
    for arm in ("bm25_dense", "bm25_dense_graph", "bm25_dense_hops", "bm25_dense_expand"):
        assert arm in ev.arms, ev.unavailable


def test_ontology_gains_are_reported_with_confidence_intervals(offline_ev):
    """n=28 上任何 ±0.02 都落在噪声里。报表必须自带 CI 与 p 值。"""
    ev = offline_ev
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


def test_metrics_are_reported_per_language(offline_ev):
    """SapBERT 是英文单语模型，中文语料上大概率无增益甚至有害。"""
    ev = offline_ev
    for arm in ev.arms.values():
        if not arm.by_lang:
            continue
        assert set(arm.by_lang) >= {"en", "zh"}
        assert sum(sub.query_count for sub in arm.by_lang.values()) == arm.query_count


def test_language_split_can_disagree_with_the_average(offline_ev):
    """分语种表必须真的能和总平均给出不同结论，否则拆分只是装饰。"""
    ev = offline_ev
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
    searcher = make_searcher(kb)
    gold = _aligned_gold(kb)
    free = eval_retrieval(
        kb,
        gold=gold,
        entitlements=frozenset(),
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
    )
    paid = eval_retrieval(
        kb,
        gold=gold,
        entitlements=LICENSED,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
    )
    n_free = len(free.arms[free.baseline].per_query)
    n_paid = len(paid.arms[paid.baseline].per_query)
    assert n_free < n_paid


# ------------------------------------------------------------------ demo


@pytest.fixture
def demo_surface(kb):
    from biomed_ontology.runtime import open_dual_surface

    searcher = make_searcher(kb)
    return open_dual_surface(
        literature_kb=kb,
        milvus_backend=searcher.backend,
        neighborhood=searcher.neighborhood,
        searcher=searcher,
    )


@pytest.mark.parametrize("demo_id", sorted(DEMOS))
def test_demo_passes(demo_surface, demo_id):
    from biomed_ontology.demo import run_demo

    result = run_demo(
        demo_id, demo_surface.kb, demo_surface.tools, foundation=demo_surface.foundation
    )
    assert result.passed, result.render()


def test_all_demos_pass_together(demo_surface):
    """写成"一条都不许失败"而不是"允许若干条失败"：坏一条必须立刻炸。"""
    results = run_all(
        demo_surface.kb, demo_surface.tools, foundation=demo_surface.foundation
    )
    failed = {r.demo_id for r in results if not r.passed}
    assert not failed, [r.render() for r in results if not r.passed]
    assert len(results) == len(DEMOS)


def test_every_demo_states_a_falsifiable_claim(demo_surface):
    """demo 必须自带断言。

    只打印一段好看的输出、不校验任何东西的"演示"，
    在下游改坏之后依然会打印那段好看的输出。
    """
    for result in run_all(
        demo_surface.kb, demo_surface.tools, foundation=demo_surface.foundation
    ):
        assert result.claim
        assert result.lines
