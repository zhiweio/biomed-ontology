"""混合检索（L5）：BM25 ⊕ 向量 ⊕ 图，带权 RRF 融合，可选交叉编码器精排。

本体在这里的价值不是替代 BM25 或向量，而是提供第三条正交通道：
BM25 找字面、向量找语义、图找"经由概念关系可达"。
前两条都基于 chunk 文本相似度，误差高度相关；图通道的误差来源完全不同，
融合后才有真正的增量 —— 只叠 BM25 和向量，提升往往落在噪声范围内。

license 过滤在候选生成阶段就介入，而不是返回前裁剪：
后者会让"总命中数"这类统计量泄漏无权数据的存在性。

词法/向量召回下沉到 Milvus（`sparse_lexical` + dense_*）；
图通道留在本层：邻接来自 GraphDB，IDF/概念倒排/RRF 仍在进程内。

本体经由**两条**路径参与检索，缺一条这个臂就名不副实：
1. 图通道 —— search-around，从查询概念沿类型化链接走到相关概念；
2. 查询改写 —— 把概念的别名喂回词法与向量通道。
早先只有第一条，于是 `expand` 开与不开对总分的差别是 +0.002。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, MappingJustificationEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.alias import normalize_alias
from biomed_ontology.corpus import Chunk
from biomed_ontology.licensing import tier_rank
from biomed_ontology.observability import Candidate, TraceContext
from biomed_ontology.ontology.neighborhood import ConceptNeighborhood
from biomed_ontology.pipeline import KnowledgeBase
from biomed_ontology.rerank import Reranker
from biomed_ontology.search.backends import (
    ChunkMeta,
    LicenseScope,
    RetrievalRequest,
    SearchBackend,
)

__all__ = [
    "CHANNEL_WEIGHTS",
    "HybridSearcher",
    "LicenseScope",
    "SearchBackend",
    "SearchHit",
    "rrf_fuse",
]

_OPEN_RANK = tier_rank(LicenseTierEnum.TIER_1)
# 对外别名：还原原文要用同一个公开档阈值，各写一份迟早对不上。
OPEN_RANK = _OPEN_RANK

# RRF 里各通道的权重。
#
# 图通道给 0.5 而不是 1.0：它的候选来自"挂了某个概念"这一个条件，
# 天然比词法/向量的相似度排序粗。等权参与融合时，它排第 3 的那个切片
# 与 BM25 排第 3 的那个切片对总分贡献相同 —— 而后者是从 588 片里
# 按相关性挑出来的，前者可能只是恰好提到了"肺癌"。
#
# 0.5 是**先验值，不是调出来的**：在同一份 28 条 gold 上搜权重再拿它报数，
# 报的就是过拟合。真要定这个值，需要一份独立的开发集。
CHANNEL_WEIGHTS: dict[RetrievalChannelEnum, float] = {
    RetrievalChannelEnum.GRAPH: 0.5,
}


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    score: float
    channel: RetrievalChannelEnum
    section: str | None = None
    snippet: str = ""
    page: int = 1
    modality: str = ""
    figure_type: str = ""
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    matched_concepts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    channel_ranks: dict[str, int] = field(default_factory=dict)
    explain: str = ""
    # 精排前的融合名次。开了精排还看不到它，就无从判断精排到底动了什么 ——
    # 一份"名次全变了但说不清为什么"的结果，下游没有复核的手段。
    rank_before_rerank: int | None = None
    rerank_score: float | None = None


def rrf_fuse(
    channel_results: dict[RetrievalChannelEnum, list[tuple[str, float]]],
    *,
    k: int = 60,
    weights: dict[RetrievalChannelEnum, float] | None = None,
) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal Rank Fusion。

    用名次而非分数融合，因为三个通道的分数量纲不可比：
    BM25 是无上界的，余弦在 [0,1]，图通道是跳数衰减。
    强行归一化分数会引入一个谁也说不清的超参，而名次天然可比。

    `weights` 表达的是另一件事：通道之间的**可信度**不同。名次可比不等于
    可信度相同 —— 一个判别力弱的通道，它的第 1 名也未必比强通道的第 5 名更该信。
    缺省不带权（全 1.0），与不加这个参数时逐位相同。
    """
    weights = weights or {}
    acc: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for channel, results in channel_results.items():
        weight = weights.get(channel, 1.0)
        for rank, (key, _score) in enumerate(results, start=1):
            acc[key] += weight / (k + rank)
            ranks[key][channel.value] = rank
    return [
        (key, score, ranks[key])
        for key, score in sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


class HybridSearcher:
    def __init__(
        self,
        kb: KnowledgeBase,
        *,
        backend: SearchBackend,
        neighborhood: ConceptNeighborhood,
    ) -> None:
        if backend is None:  # type: ignore[comparison-overlap]
            raise ValueError("检索 backend 必填（Milvus）")
        self.kb = kb
        self.backend = backend
        self.neighborhood = neighborhood
        self._by_concept: dict[str, set[str]] = defaultdict(set)
        self._chunks: dict[str, Chunk] = {}
        self._meta: dict[str, ChunkMeta] = {}

        for ch in kb.chunks:
            self._chunks[ch.chunk_id] = ch
            self._meta[ch.chunk_id] = self._chunk_meta(ch)
            for cid in ch.concept_ids:
                self._by_concept[cid].add(ch.chunk_id)
        self._concept_idf = self._build_concept_idf()
        self._concept_norm = self._build_concept_norms()

    def concept_label_terms(self, chunk: Chunk) -> list[str]:
        """索引侧注入的概念 preferred label（稀疏列文本 parity）。"""
        return self._concept_terms(chunk)

    def _build_concept_idf(self) -> dict[str, float]:
        """概念 IDF：``log(N / df)``，索引期算一次。

        高频概念若不降权，图通道会吐出大量同分候选，次级键落到 ``chunk_id``
        （哈希前缀）上，RRF 池变成近似随机样本。下界 0.1：全库挂载时 IDF 为 0
        会抹掉整条路径及沿途稀有邻居，留小正数保持连通。
        """
        total = max(len(self._chunks), 1)
        return {
            cid: max(math.log(total / len(chunks)), 0.1)
            for cid, chunks in self._by_concept.items()
            if chunks
        }

    def _build_concept_norms(self) -> dict[str, float]:
        """切片概念向量模长，供图通道余弦归一化。

        IDF 只区分概念频率；同一倒排内切片仍可能同分。除以模长后，
        专论该主题的短切片得分高于挂满概念的综述段（与稠密通道同形，
        向量空间为概念图而非字符 n-gram）。
        """
        norms: dict[str, float] = {}
        for chunk_id, chunk in self._chunks.items():
            total = sum(self._concept_idf.get(cid, 0.1) ** 2 for cid in set(chunk.concept_ids))
            norms[chunk_id] = math.sqrt(total) or 1.0
        return norms

    def _chunk_meta(self, chunk: Chunk) -> ChunkMeta:
        doc = self.kb.document(chunk.doc_id)
        return ChunkMeta(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            source_id=doc.source_id if doc else "",
            # 文档元数据缺失时按最高密级处理：宁可挡掉也不能默认放行。
            license_rank=tier_rank(doc.license_tier) if doc else tier_rank(LicenseTierEnum.TIER_3),
            labels=tuple(chunk.labels),
            modality=chunk.modality.value,
            figure_type=getattr(chunk, "figure_type", "") or "",
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
        modalities: tuple[str, ...] = (),
        figure_types: tuple[str, ...] = (),
        candidate_k: int | None = None,
        reranker: Reranker | None = None,
        rewrite: bool | None = None,
    ) -> tuple[list[SearchHit], int]:
        """检索并融合。

        `candidate_k` 是融合候选池的深度，缺省等于 `top_k`（即不加深，
        与不传这个参数时逐位相同）。开精排时它必须大于 `top_k` ——
        精排只能重排池子里已有的东西，池子多深就是它的天花板。

        `rewrite` 控制本体改写是否下发给词法/向量通道，缺省跟随 `expand`。
        拆成两个开关是为了让"图通道用了本体"和"查询串用了本体"能分开消融 ——
        合成一个开关时，一次改动同时动两处，任何结论都归因不到具体哪一处。
        """
        ent = entitlements if entitlements is not None else ctx.entitlements
        scope = LicenseScope(
            max_rank=tier_rank(max_tier), open_rank=_OPEN_RANK, entitled_sources=ent
        )
        pool_k = max(candidate_k or top_k, top_k)
        do_rewrite = expand if rewrite is None else rewrite

        # None 表示"还没归一化"，空列表表示"归一化过、一个概念也没认出来"。
        # 两者混用会让图通道在不开改写时被整条跳过 —— 而那正是它该独立起作用的配置。
        seeds: list[str] | None = None
        lexical_query, dense_queries = None, ()
        if do_rewrite and {RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE} & set(channels):
            seeds = self._seed_concepts(query, ctx)
            lexical_query, dense_queries = self._rewrite_queries(query, seeds)

        request = RetrievalRequest(
            query=query,
            scope=scope,
            top_k=pool_k,
            labels=tuple(labels or ()),
            channels=channels,
            vector_fields=vector_fields,
            modalities=modalities,
            figure_types=figure_types,
            lexical_query=lexical_query,
            dense_queries=dense_queries,
        )

        with ctx.span(
            "search", **{"hmd.query": query[:120], "hmd.backend": self.backend.name}
        ) as sp:
            result = self.backend.retrieve(request)
            results = dict(result.channels)
            filtered = result.filtered_count

            concept_ids: list[str] = seeds or []
            if RetrievalChannelEnum.GRAPH in channels:
                allowed = self._graph_allowed(request)
                graph_hits, concept_ids = self._graph_channel(query, ctx, allowed, expand, seeds)
                results[RetrievalChannelEnum.GRAPH] = graph_hits[: pool_k * 3]

            # Milvus 索引可能超前于当前进程装载的 corpus：丢弃未知 chunk_id，
            # 否则 _to_hit / 模态过滤会 KeyError，整次 search 被 ToolApi 吞成 0 hits。
            results, orphan_dropped = self._drop_unknown_chunks(results)

            fused = rrf_fuse(results, weights=CHANNEL_WEIGHTS)
            if modalities:
                # 后端已下推过滤；此处兜底 Protocol 实现忽略 modalities 的情况。
                # 必须在截断前执行，避免 top_k 被误砍薄。
                wanted = set(modalities)
                fused = [f for f in fused if self._chunks[f[0]].modality.value in wanted]
            if figure_types:
                wanted_ft = set(figure_types)
                fused = [
                    f
                    for f in fused
                    if (getattr(self._chunks[f[0]], "figure_type", "") or "") in wanted_ft
                ]
            pool = [self._to_hit(key, score, ranks) for key, score, ranks in fused[:pool_k]]
            hits = self._rerank(query, pool, reranker)[:top_k]
            sp.set(
                **{
                    "hmd.hit_count": len(hits),
                    "hmd.license_filtered": filtered,
                    "hmd.pool_size": len(pool),
                    "hmd.orphan_dropped": orphan_dropped,
                    "hmd.reranker": getattr(reranker, "name", "") if reranker else "",
                    "ontology.concept_ids": ",".join(concept_ids),
                }
            )
        return hits, filtered

    def _drop_unknown_chunks(
        self,
        channels: dict[RetrievalChannelEnum, list[tuple[str, float]]],
    ) -> tuple[dict[RetrievalChannelEnum, list[tuple[str, float]]], int]:
        """去掉不在本进程 KnowledgeBase 中的 chunk_id，返回 (过滤后通道, 丢弃数)。"""
        kept: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}
        dropped = 0
        for channel, hits in channels.items():
            alive: list[tuple[str, float]] = []
            for chunk_id, score in hits:
                if chunk_id in self._chunks:
                    alive.append((chunk_id, score))
                else:
                    dropped += 1
            if alive:
                kept[channel] = alive
        return kept, dropped

    def _seed_concepts(self, query: str, ctx: TraceContext) -> list[str]:
        res = self.kb.normalizer.normalize(query, ctx=ctx, detect=True, min_confidence=0.6)
        return res.concept_ids

    def _rewrite_queries(
        self, query: str, seeds: list[str], *, max_terms: int = 8
    ) -> tuple[str | None, tuple[str, ...]]:
        """用本体别名改写下发给词法/向量通道的查询串。

        约束：
        - 按 ``normalize_alias`` 去重，避免同一代号多种写法抬高 BM25 词频；
        - 词法侧拼接扩展词；向量侧取 (原串, 改写串) 两条编码的 max，原串始终在集合内；
        - ``max_terms`` 封顶，防止层级扩展把原始查询词稀释掉。
        """
        if not seeds:
            return None, ()
        norm = self.kb.normalizer
        weighted: dict[str, tuple[float, str]] = {}
        for cid in seeds:
            for exp in norm.expand(cid, max_depth=1, min_weight=0.35):
                key = normalize_alias(exp.term) or exp.term.casefold()
                if exp.weight > weighted.get(key, (0.0, ""))[0]:
                    weighted[key] = (exp.weight, exp.term)
        # 去掉原查询里已有的词：重复出现只会抬高它们的词频，
        # 那是在给"查询里本来就写了什么"加权，不是在扩展。
        lowered = query.casefold()
        ranked = sorted(weighted.values(), key=lambda wt: (-wt[0], wt[1]))
        terms = [term for _w, term in ranked if term.casefold() not in lowered][:max_terms]
        if not terms:
            return None, ()
        rewritten = f"{query} {' '.join(terms)}"
        return rewritten, (query, rewritten)

    def _rerank(
        self, query: str, pool: list[SearchHit], reranker: Reranker | None
    ) -> list[SearchHit]:
        """交叉编码器重排候选池。`reranker=None` 时原样返回，一次前向都不做。

        RRF 按名次投票，而名次不表达"有多相关"：三个通道各自的第 3 名进了融合，
        谁更该排前面 RRF 没有依据。精排补的就是这个依据。

        融合名次记在 `rank_before_rerank` 上而不是丢掉 —— 少了它，
        "精排把什么从第 23 名拉到了第 2 名"这件事在结果里查不出来。
        """
        if reranker is None or not pool:
            return pool
        for rank, hit in enumerate(pool, start=1):
            hit.rank_before_rerank = rank
        scores = reranker.rescore(query, [h.snippet for h in pool])
        for hit, score in zip(pool, scores, strict=True):
            hit.rerank_score = round(float(score), 6)
            hit.explain = f"{hit.explain} → rerank {score:.3f}"
        # 次级键取融合名次而不是 chunk_id：精排给出同分时（截断到 512 token 后
        # 两段看起来一样并不罕见），应当退回融合的判断，而不是退回哈希序。
        return sorted(pool, key=lambda h: (-(h.rerank_score or 0.0), h.rank_before_rerank or 0))

    def _graph_allowed(self, request: RetrievalRequest) -> set[str]:
        """图通道自己的许可、标签、模态与图型过滤，走与后端**同一组**条件。

        图通道的候选来自内存概念倒排而非后端索引，若不在此复用 `scope.permits`，
        它就会成为绕过许可隔离的旁路；modality / figure_type 同理 ——
        少过滤一条通道，"只看 CT"就会漏出一批柱状图，而调用方无从分辨是哪一路放进来的。
        """
        wanted = set(request.labels)
        modalities = set(request.modalities)
        figure_types = set(request.figure_types)
        return {
            m.chunk_id
            for m in self._meta.values()
            if request.scope.permits(m.license_rank, m.source_id)
            and (not wanted or wanted & set(m.labels))
            and (not modalities or m.modality in modalities)
            and (not figure_types or m.figure_type in figure_types)
        }

    def _graph_channel(
        self,
        query: str,
        ctx: TraceContext,
        allowed: set[str],
        expand: bool,
        seeds: list[str] | None = None,
    ) -> tuple[list[tuple[str, float]], list[str]]:
        """图通道：查询 → 概念 → search-around → 挂载这些概念的 chunk。

        打分是概念空间的 IDF 加权余弦：查询侧为种子 + 沿类型化链接的衰减邻居，
        文档侧为切片概念集合，再除以文档模长。依赖类型化邻接（非仅层级）、
        概念 IDF 与模长归一三者同时成立，否则候选易大量并列。
        """
        if seeds is None:
            seeds = self._seed_concepts(query, ctx)
        if not seeds:
            return [], []

        # 查询侧概念向量：种子权重 1.0，邻居按关系衰减。
        query_vec: dict[str, float] = {cid: 1.0 for cid in seeds}
        origin_of: dict[str, str] = {cid: cid for cid in seeds}
        if expand:
            for neighbor in self.neighborhood.neighbors(seeds, max_hops=2):
                if neighbor.weight > query_vec.get(neighbor.concept_id, 0.0):
                    query_vec[neighbor.concept_id] = neighbor.weight
                    origin_of[neighbor.concept_id] = f"{neighbor.predicate}:{neighbor.concept_id}"

        scored: dict[str, float] = defaultdict(float)
        origins: dict[str, tuple[str, float]] = {}
        for cid, qw in query_vec.items():
            idf = self._concept_idf.get(cid, 0.1)
            gain = qw * idf * idf
            for chunk_id in self._by_concept.get(cid, ()):
                if chunk_id in allowed:
                    scored[chunk_id] += gain
                    if gain > origins.get(chunk_id, ("", 0.0))[1]:
                        origins[chunk_id] = (origin_of[cid], gain)

        for chunk_id in scored:
            scored[chunk_id] /= self._concept_norm.get(chunk_id, 1.0)
        ordered = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
        ctx.record_decision(
            stage="GRAPH_RETRIEVAL",
            justification=MappingJustificationEnum.CompositeMatching,
            chosen=",".join(seeds),
            candidates=[
                Candidate(cid, sc, f"graph:{origins.get(cid, ('', 0))[0]}")
                for cid, sc in ordered[:5]
            ],
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
            modality=ch.modality.value,
            figure_type=getattr(ch, "figure_type", "") or "",
            license_tier=doc.license_tier if doc else LicenseTierEnum.TIER_0,
            matched_concepts=list(ch.concept_ids),
            labels=list(ch.labels),
            channel_ranks=ranks,
            explain=f"RRF({why})",
        )
