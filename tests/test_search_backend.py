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
        ChunkMeta(
            "CHK:open", "DOC:1", "PUBMED", tier_rank(LicenseTierEnum.TIER_0), ("efficacy",), "TEXT"
        ),
        "savolitinib MET exon 14 skipping ORR",
    )
    b.add(
        ChunkMeta("CHK:paid", "DOC:2", "MOCK_LICENSED", PAID, ("efficacy",), "TEXT"),
        "savolitinib MET exon 14 skipping competitive landscape",
    )
    b.add(
        ChunkMeta(
            "CHK:img",
            "DOC:1",
            "PUBMED",
            tier_rank(LicenseTierEnum.TIER_0),
            ("efficacy",),
            "IMAGE",
            "CHART",
        ),
        "Kaplan-Meier curve of progression-free survival for savolitinib",
    )
    b.add(
        ChunkMeta(
            "CHK:ct",
            "DOC:1",
            "PUBMED",
            tier_rank(LicenseTierEnum.TIER_0),
            ("efficacy",),
            "IMAGE",
            "RADIOLOGY",
        ),
        "chest CT scan showing a pulmonary nodule",
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


# -------------------------------------------------------------- 模态过滤


def test_modality_filter_keeps_only_that_modality(backend: LocalBackend):
    result = backend.retrieve(
        RetrievalRequest(query="savolitinib survival", scope=scope(), modalities=("IMAGE",))
    )
    assert _ids(result) <= {"CHK:img", "CHK:ct"}
    assert _ids(result)  # 至少命中一张图，否则"只剩图"是空集也能过


def test_without_the_filter_the_same_query_also_returns_text(backend: LocalBackend):
    """对照组。只断言"过滤后只剩图"是不够的 —— 一个把文本全丢掉的实现同样满足。"""
    result = backend.retrieve(RetrievalRequest(query="savolitinib survival", scope=scope()))
    assert _ids(result) >= {"CHK:img", "CHK:open"}


def test_modality_filter_does_not_inflate_the_license_filtered_count(backend: LocalBackend):
    """模态是调用方自己下的条件，不是"你无权查看"。

    混进 `filtered_count` 会让这个数字在两种完全不同的含义之间摇摆，
    而它是无权调用方判断"库里到底有没有"的唯一线索。
    """
    result = backend.retrieve(
        RetrievalRequest(query="savolitinib", scope=scope(), modalities=("IMAGE",))
    )
    assert result.filtered_count == 1  # 仅 CHK:paid，与不加模态条件时一致


def test_modality_filter_cannot_unlock_a_paid_chunk(backend: LocalBackend):
    """过滤条件是收窄，不是旁路。许可谓词仍然先行。"""
    result = backend.retrieve(
        RetrievalRequest(query="competitive landscape", scope=scope(), modalities=("TEXT",))
    )
    assert "CHK:paid" not in _ids(result)


# -------------------------------------------------------------- 图型过滤


def test_figure_type_narrows_further_than_modality_alone(backend: LocalBackend):
    """`modalities=[IMAGE]` 保证是图；`figure_types` 保证是那一类图。

    直接测候选集而不是检索命中：BM25 对"CT"本来就不会召回 Kaplan-Meier，
    那会让"模态下放行两张、图型下只放一张"这件事看不见。
    """
    by_modality, _ = backend.allow_list(
        RetrievalRequest(query="x", scope=scope(), modalities=("IMAGE",))
    )
    by_type, _ = backend.allow_list(
        RetrievalRequest(
            query="x", scope=scope(), modalities=("IMAGE",), figure_types=("RADIOLOGY",)
        )
    )
    assert by_modality == {"CHK:img", "CHK:ct"}
    assert by_type == {"CHK:ct"}


def test_figure_type_filter_does_not_inflate_the_license_filtered_count(backend: LocalBackend):
    """与模态同一条纪律：调用方自己的筛选条件不得混进许可计数。"""
    result = backend.retrieve(
        RetrievalRequest(query="chest CT", scope=scope(), figure_types=("RADIOLOGY",))
    )
    assert result.filtered_count == 1  # 仅 CHK:paid
