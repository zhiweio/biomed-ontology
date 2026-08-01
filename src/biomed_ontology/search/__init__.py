"""混合检索（L5）：BM25 ⊕ 向量 ⊕ 图，RRF 融合后重排。

本体在这里的价值不是替代 BM25 或向量，而是提供第三条正交通道：
BM25 找字面、向量找语义、图找"经由概念关系可达"。
前两条都基于 chunk 文本相似度，误差高度相关；图通道的误差来源完全不同，
融合后才有真正的增量 —— 只叠 BM25 和向量，提升往往落在噪声范围内。

license 过滤在候选生成阶段就介入，而不是返回前裁剪：
后者会让"总命中数"这类统计量泄漏无权数据的存在性。

词法/向量召回下沉到 `backends/`（本地内存或 Milvus）；
图通道留在本层，因为它依赖本体规范化器与概念倒排，向量库替不了。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, MappingJustificationEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.corpus import Chunk
from biomed_ontology.licensing import tier_rank
from biomed_ontology.observability import Candidate, TraceContext
from biomed_ontology.pipeline import KnowledgeBase
from biomed_ontology.search.backends import (
    Bm25Index,
    ChunkMeta,
    DenseIndex,
    LicenseScope,
    LocalBackend,
    RetrievalRequest,
    SearchBackend,
)

__all__ = [
    "Bm25Index",
    "DenseIndex",
    "HybridSearcher",
    "LicenseScope",
    "LocalBackend",
    "SearchBackend",
    "SearchHit",
    "rrf_fuse",
]

_OPEN_RANK = tier_rank(LicenseTierEnum.TIER_1)
# 对外别名：还原原文要用同一个公开档阈值，各写一份迟早对不上。
OPEN_RANK = _OPEN_RANK


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    score: float
    channel: RetrievalChannelEnum
    section: str | None = None
    snippet: str = ""
    page: int = 1
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    matched_concepts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    channel_ranks: dict[str, int] = field(default_factory=dict)
    explain: str = ""


def rrf_fuse(
    channel_results: dict[RetrievalChannelEnum, list[tuple[str, float]]], *, k: int = 60
) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal Rank Fusion。

    用名次而非分数融合，因为三个通道的分数量纲不可比：
    BM25 是无上界的，余弦在 [0,1]，图通道是跳数衰减。
    强行归一化分数会引入一个谁也说不清的超参，而名次天然可比。
    """
    acc: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for channel, results in channel_results.items():
        for rank, (key, _score) in enumerate(results, start=1):
            acc[key] += 1.0 / (k + rank)
            ranks[key][channel.value] = rank
    return [
        (key, score, ranks[key])
        for key, score in sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


class HybridSearcher:
    def __init__(self, kb: KnowledgeBase, backend: SearchBackend | None = None) -> None:
        self.kb = kb
        self._by_concept: dict[str, set[str]] = defaultdict(set)
        self._chunks: dict[str, Chunk] = {}
        self._meta: dict[str, ChunkMeta] = {}

        local = LocalBackend() if backend is None else None
        for ch in kb.chunks:
            self._chunks[ch.chunk_id] = ch
            self._meta[ch.chunk_id] = self._chunk_meta(ch)
            if local is not None:
                # 概念注入索引文本：让 BM25 也能命中跨别名情形，
                # 例如文中写 ORPATHYS 而查询写"沃利替尼"。
                enriched = ch.text + " " + " ".join(self._concept_terms(ch))
                local.add(self._meta[ch.chunk_id], enriched)
            for cid in ch.concept_ids:
                self._by_concept[cid].add(ch.chunk_id)
        if local is not None:
            local.build()
        self.backend: SearchBackend = local if local is not None else backend  # type: ignore[assignment]

    def _chunk_meta(self, chunk: Chunk) -> ChunkMeta:
        doc = self.kb.document(chunk.doc_id)
        return ChunkMeta(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            source_id=doc.source_id if doc else "",
            # 文档元数据缺失时按最高密级处理：宁可挡掉也不能默认放行。
            license_rank=tier_rank(doc.license_tier) if doc else tier_rank(LicenseTierEnum.TIER_3),
            labels=tuple(chunk.labels),
        )

    def chunk_meta(self, chunk_id: str) -> ChunkMeta | None:
        """对外暴露切片的许可元数据 —— 索引侧要用同一份，不能各算各的。"""
        return self._meta.get(chunk_id)

    def _concept_terms(self, chunk: Chunk) -> list[str]:
        terms = []
        for cid in chunk.concept_ids:
            c = self.kb.concept(cid)
            if c:
                terms.extend(filter(None, [c.preferred_label_en, c.preferred_label_zh]))
        return terms

    # -------------------------------------------------- 检索

    def search(
        self,
        query: str,
        *,
        ctx: TraceContext,
        top_k: int = 10,
        entitlements: frozenset[str] | None = None,
        max_tier: LicenseTierEnum = LicenseTierEnum.TIER_3,
        expand: bool = True,
        channels: tuple[RetrievalChannelEnum, ...] = (
            RetrievalChannelEnum.BM25,
            RetrievalChannelEnum.DENSE,
            RetrievalChannelEnum.GRAPH,
        ),
        labels: list[str] | None = None,
        vector_fields: tuple[str, ...] = (),
    ) -> tuple[list[SearchHit], int]:
        ent = entitlements if entitlements is not None else ctx.entitlements
        scope = LicenseScope(
            max_rank=tier_rank(max_tier), open_rank=_OPEN_RANK, entitled_sources=ent
        )
        request = RetrievalRequest(
            query=query,
            scope=scope,
            top_k=top_k,
            labels=tuple(labels or ()),
            channels=channels,
            vector_fields=vector_fields,
        )

        with ctx.span(
            "search", **{"hmd.query": query[:120], "hmd.backend": self.backend.name}
        ) as sp:
            result = self.backend.retrieve(request)
            results = dict(result.channels)
            filtered = result.filtered_count

            concept_ids: list[str] = []
            if RetrievalChannelEnum.GRAPH in channels:
                allowed = self._graph_allowed(request)
                graph_hits, concept_ids = self._graph_channel(query, ctx, allowed, expand)
                results[RetrievalChannelEnum.GRAPH] = graph_hits[: top_k * 3]

            fused = rrf_fuse(results)
            hits = [self._to_hit(key, score, ranks) for key, score, ranks in fused[:top_k]]
            sp.set(
                **{
                    "hmd.hit_count": len(hits),
                    "hmd.license_filtered": filtered,
                    "ontology.concept_ids": ",".join(concept_ids),
                }
            )
        return hits, filtered

    def _graph_allowed(self, request: RetrievalRequest) -> set[str]:
        """图通道自己的许可过滤，走与后端**同一个**谓词。

        图通道的候选来自内存概念倒排而非后端索引，若不在此复用 `scope.permits`，
        它就会成为绕过许可隔离的旁路。
        """
        wanted = set(request.labels)
        return {
            m.chunk_id
            for m in self._meta.values()
            if request.scope.permits(m.license_rank, m.source_id)
            and (not wanted or wanted & set(m.labels))
        }

    def _graph_channel(
        self, query: str, ctx: TraceContext, allowed: set[str], expand: bool
    ) -> tuple[list[tuple[str, float]], list[str]]:
        """图通道：查询 → 概念 → （层级扩展）→ 挂载了这些概念的 chunk。

        深度衰减 0.8：一层之外的关联仍然有用，但不该盖过字面直击。
        """
        norm = self.kb.normalizer
        res = norm.normalize(query, ctx=ctx, detect=True, min_confidence=0.6)
        seeds = res.concept_ids
        scored: dict[str, float] = defaultdict(float)
        for cid in seeds:
            for chunk_id in self._by_concept.get(cid, ()):
                if chunk_id in allowed:
                    scored[chunk_id] = max(scored[chunk_id], 1.0)
            if not expand:
                continue
            for depth in (1, 2):
                for desc in norm.descendants(cid, max_depth=depth):
                    if desc in seeds:
                        continue
                    for chunk_id in self._by_concept.get(desc, ()):
                        if chunk_id in allowed:
                            scored[chunk_id] = max(scored[chunk_id], 0.8**depth)
        ordered = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
        if seeds:
            ctx.record_decision(
                stage="GRAPH_RETRIEVAL",
                justification=MappingJustificationEnum.CompositeMatching,
                chosen=",".join(seeds),
                candidates=[Candidate(cid, sc, "graph") for cid, sc in ordered[:5]],
                state_before=query[:120],
                state_after=f"graph_hits={len(ordered)}",
            )
        return ordered, seeds

    def _to_hit(self, chunk_id: str, score: float, ranks: dict[str, int]) -> SearchHit:
        ch = self._chunks[chunk_id]
        doc = self.kb.document(ch.doc_id)
        why = " + ".join(f"{c}#{r}" for c, r in sorted(ranks.items(), key=lambda kv: kv[1]))
        return SearchHit(
            chunk_id=chunk_id,
            doc_id=ch.doc_id,
            score=round(score, 6),
            channel=RetrievalChannelEnum.FUSED,
            section=ch.section,
            snippet=ch.text[:300],
            page=ch.page,
            license_tier=doc.license_tier if doc else LicenseTierEnum.TIER_0,
            matched_concepts=list(ch.concept_ids),
            labels=list(ch.labels),
            channel_ranks=ranks,
            explain=f"RRF({why})",
        )
