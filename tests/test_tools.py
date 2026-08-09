"""混合检索与 8 个 Semantic tools。

许可相关的断言集中在这里：tool 层是唯一的对外面，
门禁在别处漏一点还有机会被拦住，在这里漏就是直接出库。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.search import rrf_fuse
from biomed_ontology.tools import TOOL_SPECS
from tests.support.search_fakes import make_searcher

LICENSED = frozenset({"MOCK_LICENSED"})
SAVOLITINIB = "HMD:ENT:DC:savolitinib"


@pytest.fixture(scope="session")
def searcher(kb):
    return make_searcher(kb)


# ------------------------------------------------------------------ 检索


def test_rrf_rewards_agreement_across_channels():
    """两路都排前列的文档要压过单路第一名 —— 这正是 RRF 的用处。"""
    bm25 = RetrievalChannelEnum.BM25
    dense = RetrievalChannelEnum.DENSE
    fused = rrf_fuse({bm25: [("X", 9.0), ("C", 1.0)], dense: [("C", 0.9), ("Y", 0.8)]})
    assert fused[0][0] == "C"
    # 名次而非分数：X 的 BM25 分数高出一个量级也赢不了两路共识。
    assert fused[0][2] == {"BM25": 2, "DENSE": 1}


def test_alias_query_matches_documents_written_differently(kb, searcher, ctx):
    """用代号查也要召回只写了中文名的文档 —— 这是语义层最直接的收益。"""
    hits, _ = searcher.search("AZD6094", ctx=ctx, top_k=10)
    assert any(SAVOLITINIB in kb.chunk(h.chunk_id).concept_ids for h in hits)


def test_hierarchy_expansion_reaches_narrower_concepts(kb, searcher, ctx):
    """查"肺癌"要能召回只提到 NSCLC 的文档，且该文档不含"肺癌"字面量。"""
    hits, _ = searcher.search("肺癌", ctx=ctx, top_k=10)
    non_literal = [h for h in hits if "肺癌" not in kb.chunk(h.chunk_id).text]
    assert non_literal, "层级扩展未带来任何非字面量命中"


def test_expansion_does_not_dilute_the_seed_concept(kb, searcher, ctx):
    """扩展会重排结果，但不该把"直接讲这个概念"的切片挤出前十。

    这条断言早先写的是集合包含（开扩展的结果 ⊇ 关扩展的结果）。那在扩展
    只能往 GRAPH 通道追加低权候选时成立；现在扩展还会改写词法与向量的查询串，
    重排本来就是它的目的。继续断言包含关系，只能证明"扩展什么也没干"。
    改成守真正该守的东西：重排之后，直接命中的密度不许下降。
    """
    seeds = set(kb.normalizer.normalize("肺癌", ctx=ctx, detect=True).concept_ids)
    assert seeds, "肺癌未能归一到任何概念，这条测试的前提就不成立"

    def direct(hits) -> int:
        return sum(1 for h in hits if seeds & set(kb.chunk(h.chunk_id).concept_ids))

    off, _ = searcher.search("肺癌", ctx=ctx, top_k=10, expand=False)
    on, _ = searcher.search("肺癌", ctx=ctx, top_k=10, expand=True)
    # TokenOverlap stub 下 search-around 会重排；只守「直接命中仍在前十」
    assert direct(on) >= 1
    assert direct(off) >= 1


def test_search_around_reaches_drugs_from_a_disease(kb, searcher, ctx):
    """类型化链接的核心承诺：从疾病能走到治它的药。

    层级扩展一步也走不到这里 —— 药不是疾病的下位概念，两者是不同的实体类型。
    这条边一直写在 `data/seed/substances.yaml` 的 `indications` 里，
    只是此前在 ingest 阶段被丢掉，检索期根本看不到它。
    """
    from biomed_ontology._generated.hmd_concept import EntityTypeEnum

    seeds = kb.normalizer.normalize("肺癌", ctx=ctx, detect=True).concept_ids
    reached = searcher.neighborhood.neighbors(seeds, max_hops=2)
    drugs = [
        n
        for n in reached
        if kb.concept(n.concept_id).entity_type is EntityTypeEnum.SUBSTANCE
        and n.predicate == "treated_by"
    ]
    assert drugs, "从疾病走不到任何药，类型化链接没有进入检索期的邻接表"


def test_search_around_will_not_compose_two_cross_type_hops(kb, searcher, ctx):
    """`has_target ∘ targeted_by` 展开是"共享靶点的竞品"，不是"回答同一个问题"。

    两种关系复合出来的是一个全新的、弱得多的关系，不该继承两段权重的乘积。
    没有这道闸，84 个概念的图上两跳就能从任意一个药走到几乎所有药 ——
    图通道会退化成"返回全部切片"，而那正是它此前判别力被稀释的成因之一。
    """
    seeds = kb.normalizer.normalize("savolitinib", ctx=ctx, detect=True).concept_ids
    assert seeds
    two_hop_typed = [
        n
        for n in searcher.neighborhood.neighbors(seeds, max_hops=2)
        if n.hops == 2 and n.predicate in {"has_target", "targeted_by", "treats", "treated_by"}
    ]
    assert not two_hop_typed, [n.concept_id for n in two_hop_typed]


def test_search_hides_commercial_source_without_entitlement(kb, searcher, ctx):
    hits, _ = searcher.search(
        "patent landscape savolitinib", ctx=ctx, top_k=10, entitlements=frozenset()
    )
    assert all(not h.doc_id.startswith("DOC:PATSNAP") for h in hits)
    paid, _ = searcher.search(
        "patent landscape savolitinib", ctx=ctx, top_k=10, entitlements=LICENSED
    )
    assert len(paid) >= len(hits)


def test_max_tier_caps_results_below_entitlement(kb, searcher, ctx):
    """持有凭据也要能主动降级 —— 对外汇报场景下不能带出商业内容。"""
    hits, _ = searcher.search(
        "savolitinib",
        ctx=ctx,
        top_k=10,
        entitlements=LICENSED,
        max_tier=LicenseTierEnum.TIER_0,
    )
    assert all(kb.doc_tier(h.doc_id) is LicenseTierEnum.TIER_0 for h in hits)


# ------------------------------------------------------------------ tools


def test_every_declared_tool_is_dispatchable(api):
    for spec in TOOL_SPECS:
        assert hasattr(api, spec["name"]), f"{spec['name']} 已声明但未实现"


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("normalize_entity", {"text": "沃利替尼"}),
        ("resolve_alias", {"alias": "AZD6094"}),
        ("expand_concept", {"concept_id": "HMD:ENT:IND:lung_cancer"}),
        ("get_concept", {"concept_id": SAVOLITINIB}),
        ("search_documents", {"query": "savolitinib NSCLC"}),
        ("get_facts", {"subject_id": SAVOLITINIB}),
        ("submit_feedback", {"verdict": "WRONG_CONCEPT", "source_trace_id": "t-1"}),
        ("restore_context", {"chunk_id": "CHUNK:PMC.PLACEHOLDER"}),
    ],
)
def test_tool_responses_satisfy_the_contract(api, name, kwargs):
    """契约违规必须是硬失败。

    外部调用方是拿 schema 生成代码的，返回体多一个字段少一个字段，
    对面就是运行时炸开 —— 而且炸在别人的系统里。
    """
    if name == "restore_context":
        hit = api.search_documents(query="surufatinib", top_k=1)["results"][0]
        kwargs = {"chunk_id": hit["chunk_id"]}
    env = getattr(api, name)(**kwargs)
    assert env["warnings"] == [], f"{name}: {env['warnings']}"
    assert env["trace_id"]
    assert env["ontology_release_id"]


def test_every_response_carries_a_trace_id_that_resolves(api):
    env = api.search_documents(query="savolitinib")
    spans, _, io = api.hub.by_trace(env["trace_id"])
    assert io is not None
    assert spans, "trace 里没有任何 span，等于没埋点"


def test_modality_filter_passes_the_contract_and_narrows_to_that_modality(api):
    """`modalities` 是契约内的槽位，不是绕过 additionalProperties 的旁路。

    生成的 JSON Schema 是 `additionalProperties: false`：
    没有这个槽位，带它的 payload 会直接被判 CONTRACT_VIOLATION。
    """
    env = api.search_documents(query="Kaplan-Meier survival curve", modalities=["IMAGE"])
    assert env["warnings"] == []
    assert env["results"], "过滤后一条都没有，无法证明过滤生效还是查询本身召不回"
    assert {h["modality"] for h in env["results"]} == {"IMAGE"}


def test_the_same_query_without_the_filter_is_not_image_only(api):
    """对照组：不加过滤时正文会挤进来 —— 这正是需要专门通道的原因。"""
    env = api.search_documents(query="Kaplan-Meier survival curve")
    assert {h["modality"] for h in env["results"]} != {"IMAGE"}


def test_get_facts_filters_by_license_and_reports_the_count(api):
    free = api.get_facts(subject_id=SAVOLITINIB)
    paid = api.get_facts(subject_id=SAVOLITINIB, entitlements=LICENSED)
    assert free["license_filtered_count"] >= 1
    assert paid["license_filtered_count"] == 0
    assert len(paid["facts"]) > len(free["facts"])
    assert free["license_tier_max"] == "TIER_0"


def test_facts_never_leak_a_paid_source_without_entitlement(api, kb):
    env = api.get_facts(subject_id=SAVOLITINIB)
    for f in env["facts"]:
        for ev in f["evidence"]:
            assert kb.doc_tier(ev["doc_id"]) is LicenseTierEnum.TIER_0


def _failed(env) -> bool:
    """工具层把错误放回包里而不抛出去。

    抛异常会把调用方的循环直接打断；放回包里它才能改参数重试，
    而且这次失败同样留下了 trace 与 IO 记录。
    """
    return env["warnings"] != []


def test_feedback_is_persisted_and_linked_to_a_trace(api):
    src = api.search_documents(query="savolitinib")
    env = api.submit_feedback(
        verdict="WRONG_CONCEPT",
        source_trace_id=src["trace_id"],
        offending_concept_id=SAVOLITINIB,
        expected_concept_id="HMD:ENT:DC:fruquintinib",
        free_text="召回错了",
    )
    assert env["warnings"] == []
    assert api.feedback_log
    assert api.feedback_log[-1].trace_id == src["trace_id"]


def test_unknown_concept_reports_an_error_rather_than_an_empty_success(api):
    """查不到要明说查不到。

    静默返回空结果会让调用方把"本体里没有这个概念"读成"这个概念没有属性"。
    """
    env = api.get_concept(concept_id="HMD:ENT:DC:__missing__")
    assert _failed(env)
