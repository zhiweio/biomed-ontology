"""Milvus 后端：许可过滤必须被**证明**在起作用，不是被假设。

没有 Docker 时跳过而非失败 —— 一个因环境缺失而红的 CI，
很快就会被所有人习惯性忽略，连带真实失败一起。
"""

from __future__ import annotations

import os

import pytest

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.embed import FakeEmbedder
from biomed_ontology.search.backends.base import LicenseScope, RetrievalRequest
from biomed_ontology.search.backends.milvus import MilvusBackend

MILVUS_URI = os.environ.get("HMD_MILVUS_URI", "http://localhost:19530")


def _reachable() -> bool:
    """注意：**不**在模块顶层 importorskip。

    表达式与注入防御的用例是纯 Python，跟 pymilvus 无关。把它们一起跳过，
    等于在没装驱动的机器上静默地关掉了一道安全闸门。
    """
    try:
        from pymilvus import MilvusClient

        MilvusClient(uri=MILVUS_URI).list_collections()
    except Exception:
        return False
    return True


requires_milvus = pytest.mark.skipif(
    not _reachable(),
    reason=f"Milvus 未就绪（{MILVUS_URI}）；启动 docker/milvus-standalone.yml 后重跑",
)

OPEN_SCOPE = LicenseScope(max_rank=3, open_rank=1)
PAID_SCOPE = LicenseScope(max_rank=3, open_rank=1, entitled_sources=frozenset({"PATSNAP"}))

ROWS = [
    {
        "chunk_id": "CHK:open1",
        "doc_id": "DOC:PMC.1",
        "source_id": "PMC",
        "license_rank": 0,
        "section_id": "SEC:1",
        "section_path": "Results",
        "sort_order": 0,
        "page": 4,
        "modality": "TEXT",
        "degraded": "",
        "labels": ["EFFICACY"],
        "concept_ids_expanded": ["HMD:SUB.0001"],
        "text": "Savolitinib objective response rate was 49.2 percent in the trial cohort.",
    },
    {
        "chunk_id": "CHK:paid1",
        "doc_id": "DOC:PATSNAP.1",
        "source_id": "PATSNAP",
        "license_rank": 3,
        "section_id": "SEC:2",
        "section_path": "Claims",
        "sort_order": 0,
        "page": 2,
        "modality": "TEXT",
        "degraded": "",
        "labels": ["EFFICACY"],
        "concept_ids_expanded": ["HMD:SUB.0001"],
        "text": "Savolitinib objective response rate was 49.2 percent per licensed analytics.",
    },
    {
        "chunk_id": "CHK:open2",
        "doc_id": "DOC:PMC.1",
        "source_id": "PMC",
        "license_rank": 0,
        "section_id": "SEC:3",
        "section_path": "image:F1",
        "sort_order": 0,
        "page": 5,
        "modality": "IMAGE",
        "degraded": "",
        "labels": ["EFFICACY"],
        "concept_ids_expanded": ["HMD:SUB.0001"],
        "text": "Figure 1. Savolitinib objective response rate by subgroup.",
    },
]

QUERY = "savolitinib objective response rate"


# ------------------------------------------------------- 表达式（无需 Docker）


def test_expression_matches_the_python_predicate():
    """`milvus_expr` 与 `permits` 必须表达同一件事，两者不能各自漂移。"""
    expr = OPEN_SCOPE.milvus_expr()
    assert "license_rank <= 3" in expr
    assert "license_rank <= 1" in expr
    assert "source_id" not in expr, "无凭据时不该出现来源白名单"


def test_entitlement_appears_in_the_expression():
    expr = PAID_SCOPE.milvus_expr()
    assert 'source_id in ["PATSNAP"]' in expr


def test_entitlements_are_intersected_with_the_registry():
    """凭据是客户端自述。不与已登记来源求交集就等于让调用方自己定义许可边界。"""
    scope = LicenseScope(
        max_rank=3, open_rank=1, entitled_sources=frozenset({"PATSNAP", "MADE_UP"})
    )
    expr = scope.milvus_expr(known_sources=frozenset({"PATSNAP", "PMC"}))
    assert "PATSNAP" in expr
    assert "MADE_UP" not in expr


def test_intersection_to_empty_falls_back_to_open_only():
    scope = LicenseScope(max_rank=3, open_rank=1, entitled_sources=frozenset({"MADE_UP"}))
    expr = scope.milvus_expr(known_sources=frozenset({"PMC"}))
    assert "source_id" not in expr


@pytest.mark.parametrize(
    "evil",
    ['PMC" or license_rank <= 3 or source_id == "', "PMC'; drop", "PMC or true", 'a"b'],
)
def test_expression_injection_is_rejected_not_escaped(evil: str):
    """转义看起来友好，却会让一条本该被发现的脏数据静静流进查询。"""
    scope = LicenseScope(max_rank=3, open_rank=1, entitled_sources=frozenset({evil}))
    with pytest.raises(ValueError, match="非法字符"):
        scope.milvus_expr()


@pytest.mark.parametrize(
    "evil",
    ['DOC:X" or license_rank <= 3 or doc_id == "', 'a"b', "x or true", "DOC:X'; drop"],
)
def test_restore_identifiers_are_validated(evil: str):
    """还原原文的入参来自调用方。不验形状就等于把许可边界交给调用方。"""
    be = MilvusBackend(collection="unused", embedder=FakeEmbedder(), client=object())
    with pytest.raises(ValueError, match="非法字符"):
        be.restore_section(evil, "SEC:1", RetrievalRequest(query="", scope=OPEN_SCOPE))


def test_modality_filter_is_pushed_down_alongside_the_license_predicate():
    """模态过滤下推而非取回后再筛。

    在库外筛意味着 `limit` 先砍在混排结果上 —— 图像只占语料 6%，
    等结果回到进程里再筛，那一批里往往一张图都没有。
    """
    be = MilvusBackend(collection="unused", embedder=FakeEmbedder(), client=object())
    expr = be._filter(
        RetrievalRequest(query=QUERY, scope=OPEN_SCOPE, modalities=("IMAGE", "TABLE"))
    )
    assert 'modality in ["IMAGE", "TABLE"]' in expr
    assert "license_rank <= 1" in expr, "模态条件不得顶替许可谓词"


def test_license_filtered_count_ignores_the_callers_own_conditions():
    """`filtered_count` 只算许可挡掉的。

    把 labels / modality 也算进去，这个字段就在"你无权查看"和"你自己筛掉的"
    之间摇摆，而本地后端算的一直是前者 —— 同一字段在两个后端含义不同是最坏的情形。
    """
    be = MilvusBackend(collection="unused", embedder=FakeEmbedder(), client=object())
    request = RetrievalRequest(
        query=QUERY, scope=OPEN_SCOPE, modalities=("IMAGE",), labels=("EFFICACY",)
    )
    assert be._license_expr(request) == OPEN_SCOPE.milvus_expr()
    assert "modality" not in be._license_expr(request)


# --------------------------------------------------------- 需要真实 Milvus


@pytest.fixture(scope="module")
def backend():
    be = MilvusBackend(
        uri=MILVUS_URI,
        collection="hmd_test_chunks",
        embedder=FakeEmbedder(),
        known_sources=frozenset({"PMC", "PATSNAP"}),
    )
    be.ensure_collection(drop_existing=True)
    be.upsert(ROWS)
    be.client.load_collection("hmd_test_chunks")
    yield be
    be.client.drop_collection("hmd_test_chunks")


@requires_milvus
def test_filter_is_load_bearing(backend):
    """同一条 query 跑两次：无凭据拿不到，有凭据拿得到。

    只断言"拿不到"是不够的 —— 查询打错字同样拿不到。
    必须证明是过滤在起作用，而不是运气。
    """
    without = backend.retrieve(RetrievalRequest(query=QUERY, scope=OPEN_SCOPE, top_k=10))
    with_ent = backend.retrieve(RetrievalRequest(query=QUERY, scope=PAID_SCOPE, top_k=10))

    def ids(result):
        return {cid for hits in result.channels.values() for cid, _ in hits}

    assert "CHK:paid1" not in ids(without)
    assert "CHK:open1" in ids(without), "过滤不该连公开内容一起挡掉"
    assert "CHK:paid1" in ids(with_ent), "有凭据却拿不到 —— 那是查询问题，不是过滤生效"


@requires_milvus
def test_filtered_count_is_reported(backend):
    result = backend.retrieve(RetrievalRequest(query=QUERY, scope=OPEN_SCOPE, top_k=10))
    assert result.filtered_count >= 1, "被挡掉多少条是无权调用方唯一的线索"


@requires_milvus
@pytest.mark.parametrize(
    "field", ["sparse_lexical", "dense_general", "dense_biomed", "dense_visual"]
)
def test_each_vector_column_is_independently_queryable(backend, field: str):
    """四列必须能逐列单独检索，否则逐列消融根本做不出来。"""
    result = backend.retrieve(
        RetrievalRequest(query=QUERY, scope=PAID_SCOPE, top_k=5, vector_fields=(field,))
    )
    assert any(hits for hits in result.channels.values())


@requires_milvus
def test_lexical_and_dense_land_in_different_channels(backend):
    """通道分开才能事后归因"这条是词法命中还是语义命中"。"""
    result = backend.retrieve(
        RetrievalRequest(
            query=QUERY,
            scope=PAID_SCOPE,
            top_k=5,
            vector_fields=("sparse_lexical", "dense_general"),
        )
    )
    assert RetrievalChannelEnum.BM25 in result.channels
    assert RetrievalChannelEnum.DENSE in result.channels


@requires_milvus
def test_modality_filter_actually_narrows_what_milvus_returns(backend):
    """表达式拼对了不等于库照着执行。

    `test_modality_filter_is_pushed_down_alongside_the_license_predicate` 断言的是
    字符串形态 —— 那条能过、而 Milvus 仍然把文本返回回来的情形是存在的
    （字段名拼错、类型不匹配都会被解析成恒真）。这条打真库。
    """

    def ids(result):
        return {cid for hits in result.channels.values() for cid, _ in hits}

    unfiltered = ids(backend.retrieve(RetrievalRequest(query=QUERY, scope=PAID_SCOPE, top_k=10)))
    images = ids(
        backend.retrieve(
            RetrievalRequest(query=QUERY, scope=PAID_SCOPE, top_k=10, modalities=("IMAGE",))
        )
    )
    assert unfiltered >= {"CHK:open1", "CHK:open2"}, "对照组：不加过滤时文本与图都在"
    assert images == {"CHK:open2"}


@requires_milvus
def test_section_restore_respects_the_same_predicate(backend):
    """还原原文不能成为绕过许可的后门。"""
    allowed = backend.restore_section(
        "DOC:PATSNAP.1", "SEC:2", RetrievalRequest(query="", scope=PAID_SCOPE)
    )
    denied = backend.restore_section(
        "DOC:PATSNAP.1", "SEC:2", RetrievalRequest(query="", scope=OPEN_SCOPE)
    )
    assert allowed
    assert denied == []
