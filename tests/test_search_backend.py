"""检索后端协议层：许可谓词 + Milvus 过滤表达式下推（mock client）。

`LicenseScope.permits` 是本仓库唯一的许可判定谓词 —— Milvus 后端翻译成
标量过滤表达式，两者必须共用它。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.licensing import tier_rank
from biomed_ontology.search.backends import ChunkMeta, LicenseScope, RetrievalRequest
from biomed_ontology.search.backends.milvus import MilvusBackend

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


# -------------------------------------------------------------- Milvus 过滤下推


class _FakeEmbedder:
    name = "fake"

    def encode(self, texts: list[str]) -> list[dict[str, Any]]:
        out = []
        for _ in texts:
            out.append(
                {
                    "sparse_lexical": {1: 1.0},
                    "dense_general": [0.1] * 8,
                }
            )
        return out

    @property
    def dims(self) -> dict[str, int]:
        return {"dense_general": 8}


def _backend_with_mock_client(*, fields: tuple[str, ...] = ("sparse_lexical", "dense_general")):
    client = MagicMock()
    client.has_collection.return_value = True
    client.describe_collection.return_value = {
        "fields": [{"name": f} for f in fields],
        "description": "embedder=fake",
    }
    client.search.return_value = [[{"entity": {"chunk_id": "CHK:open"}, "distance": 0.9}]]
    client.query.return_value = [{"count(*)": 2}]
    be = MilvusBackend(
        collection="hmd_test",
        embedder=_FakeEmbedder(),
        client=client,
        known_sources=frozenset({"PUBMED", "MOCK_LICENSED"}),
    )
    return be, client


def test_milvus_filter_pushes_license_and_modality():
    be, _ = _backend_with_mock_client()
    expr = be._filter(
        RetrievalRequest(query="x", scope=scope(), modalities=("IMAGE",), labels=("efficacy",))
    )
    assert "license_rank" in expr
    assert 'modality in ["IMAGE"]' in expr
    assert "ARRAY_CONTAINS_ANY(labels" in expr


def test_milvus_figure_type_narrows_filter():
    be, _ = _backend_with_mock_client()
    expr = be._filter(
        RetrievalRequest(
            query="x",
            scope=scope(),
            modalities=("IMAGE",),
            figure_types=("RADIOLOGY",),
        )
    )
    assert 'figure_type in ["RADIOLOGY"]' in expr


def test_bm25_channel_searches_sparse_lexical_only():
    be, client = _backend_with_mock_client()
    be.retrieve(
        RetrievalRequest(
            query="savolitinib",
            scope=scope(),
            channels=(RetrievalChannelEnum.BM25,),
            vector_fields=("sparse_lexical",),
        )
    )
    assert client.search.called
    assert all(c.kwargs["anns_field"] == "sparse_lexical" for c in client.search.call_args_list)


def test_missing_sparse_lexical_hard_fails_for_bm25():
    be, _ = _backend_with_mock_client(fields=("dense_general",))
    with pytest.raises(RuntimeError, match="sparse_lexical"):
        be.retrieve(
            RetrievalRequest(
                query="savolitinib",
                scope=scope(),
                channels=(RetrievalChannelEnum.BM25,),
            )
        )


def test_channels_are_independently_selectable():
    be, client = _backend_with_mock_client()
    result = be.retrieve(
        RetrievalRequest(
            query="savolitinib",
            scope=scope(),
            channels=(RetrievalChannelEnum.BM25,),
            vector_fields=("sparse_lexical",),
        )
    )
    assert set(result.channels) == {RetrievalChannelEnum.BM25}
    assert client.search.call_count == 1
