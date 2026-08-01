"""检索后端协议层：许可谓词与本地后端。

`LicenseScope.permits` 是本仓库唯一的许可判定谓词 —— 本地后端跑成 Python 循环、
Milvus 后端翻译成标量过滤表达式，两者必须共用它。谓词一旦分叉，
就会出现"本地测试通过、线上泄漏"的最坏情形，因此这里的断言按安全测试对待。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.licensing import tier_rank
from biomed_ontology.search.backends import (
    ChunkMeta,
    LicenseScope,
    LocalBackend,
    RetrievalRequest,
)

OPEN = tier_rank(LicenseTierEnum.TIER_1)
PAID = tier_rank(LicenseTierEnum.TIER_3)


def scope(*, max_tier=LicenseTierEnum.TIER_3, sources=frozenset()) -> LicenseScope:
    return LicenseScope(max_rank=tier_rank(max_tier), open_rank=OPEN, entitled_sources=sources)


# -------------------------------------------------------------- 许可谓词


def test_open_tiers_need_no_entitlement():
    s = scope()
    assert s.permits(tier_rank(LicenseTierEnum.TIER_0), "PUBMED")
    assert s.permits(OPEN, "CHEMBL")


def test_paid_tier_requires_matching_source_entitlement():
    assert not scope().permits(PAID, "MOCK_LICENSED")
    assert scope(sources=frozenset({"MOCK_LICENSED"})).permits(PAID, "MOCK_LICENSED")


def test_entitlement_does_not_cross_sources():
    """持有 A 源的凭据不得解锁 B 源 —— 采购边界是逐源的。"""
    s = scope(sources=frozenset({"MOCK_LICENSED"}))
    assert not s.permits(PAID, "PATSNAP")


def test_caller_tier_cap_overrides_entitlement():
    """调用方自愿降级时，凭据也不能把 tier 提回来。"""
    s = scope(max_tier=LicenseTierEnum.TIER_1, sources=frozenset({"MOCK_LICENSED"}))
    assert not s.permits(PAID, "MOCK_LICENSED")


# -------------------------------------------------------------- 本地后端


@pytest.fixture
def backend() -> LocalBackend:
    b = LocalBackend()
    b.add(
        ChunkMeta("CHK:open", "DOC:1", "PUBMED", tier_rank(LicenseTierEnum.TIER_0), ("efficacy",)),
        "savolitinib MET exon 14 skipping ORR",
    )
    b.add(
        ChunkMeta("CHK:paid", "DOC:2", "MOCK_LICENSED", PAID, ("efficacy",)),
        "savolitinib MET exon 14 skipping competitive landscape",
    )
    b.build()
    return b


def _ids(result) -> set[str]:
    return {cid for hits in result.channels.values() for cid, _ in hits}


def test_paid_chunk_is_absent_without_entitlement(backend: LocalBackend):
    result = backend.retrieve(RetrievalRequest(query="savolitinib MET", scope=scope()))
    assert "CHK:paid" not in _ids(result)
    assert result.filtered_count == 1


def test_same_query_returns_paid_chunk_with_entitlement(backend: LocalBackend):
    """与上一条构成对照：证明是过滤器在起作用，而不是这条切片本来就召不回。

    只断言"拿不到"是不够的 —— 查询打错字同样拿不到。
    """
    result = backend.retrieve(
        RetrievalRequest(query="savolitinib MET", scope=scope(sources=frozenset({"MOCK_LICENSED"})))
    )
    assert "CHK:paid" in _ids(result)
    assert result.filtered_count == 0


def test_filtered_count_is_reported_not_hidden(backend: LocalBackend):
    """无权调用方要能区分"没有这份资料"与"有但你看不到"。"""
    result = backend.retrieve(RetrievalRequest(query="不存在的词", scope=scope()))
    assert result.filtered_count == 1
    assert _ids(result) == set()


def test_label_filter_narrows_candidates(backend: LocalBackend):
    result = backend.retrieve(
        RetrievalRequest(query="savolitinib", scope=scope(), labels=("safety",))
    )
    assert _ids(result) == set()


def test_channels_are_independently_selectable(backend: LocalBackend):
    """逐通道可关是 P13 消融的前提。"""
    result = backend.retrieve(
        RetrievalRequest(query="savolitinib", scope=scope(), channels=(RetrievalChannelEnum.BM25,))
    )
    assert set(result.channels) == {RetrievalChannelEnum.BM25}
