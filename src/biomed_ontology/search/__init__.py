"""混合检索（L5）：BM25 ⊕ 向量 ⊕ 图，RRF 融合后重排。

本体在这里的价值不是替代 BM25 或向量，而是提供第三条正交通道：
BM25 找字面、向量找语义、图找"经由概念关系可达"。
前两条都基于 chunk 文本相似度，误差高度相关；图通道的误差来源完全不同，
融合后才有真正的增量 —— 只叠 BM25 和向量，提升往往落在噪声范围内。

license 过滤在候选生成阶段就介入，而不是返回前裁剪：
后者会让"总命中数"这类统计量泄漏无权数据的存在性。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, MappingJustificationEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.alias import normalize_alias
from biomed_ontology.corpus import Chunk
from biomed_ontology.licensing import tier_rank
from biomed_ontology.observability import Candidate, TraceContext
from biomed_ontology.pipeline import KnowledgeBase

__all__ = ["Bm25Index", "HybridSearcher", "SearchHit", "rrf_fuse"]

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    return [normalize_alias(t) or t.casefold() for t in _TOKEN.findall(text)]


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


class Bm25Index:
    """Okapi BM25。自实现而非引入 elasticsearch：PoC 要能离线可重放，
    外部服务会把评测结果和某一次索引状态绑死。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: dict[str, Counter[str]] = {}
        self._len: dict[str, int] = {}
        self._df: Counter[str] = Counter()
        self._avgdl = 0.0

    def add(self, key: str, text: str) -> None:
        tf = Counter(_tokens(text))
        self._docs[key] = tf
        self._len[key] = sum(tf.values())
        for term in tf:
            self._df[term] += 1
        self._avgdl = sum(self._len.values()) / max(1, len(self._len))

    def search(self, query: str, *, allowed: set[str] | None = None, top_k: int = 20):
        n = len(self._docs)
        if not n:
            return []
        q = _tokens(query)
        scores: dict[str, float] = defaultdict(float)
        for term in q:
            df = self._df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for key, tf in self._docs.items():
                if allowed is not None and key not in allowed:
                    continue
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = self._len[key]
                denom = f + self.k1 * (1 - self.b + self.b * dl / max(1e-9, self._avgdl))
                scores[key] += idf * (f * (self.k1 + 1)) / denom
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]


class DenseIndex:
    """字符 3-gram TF-IDF 余弦。刻意不用预训练向量模型。

    PoC 阶段引入外部 embedding 会让"本体带来多少增量"这个核心问题
    被"换个 embedding 会不会更好"淹没。生产替换点就是这个类，接口不变。
    """

    def __init__(self, n: int = 3) -> None:
        self.n = n
        self._vecs: dict[str, dict[str, float]] = {}
        self._idf: dict[str, float] = {}
        self._raw: dict[str, Counter[str]] = {}

    def add(self, key: str, text: str) -> None:
        self._raw[key] = Counter(self._grams(text))

    def build(self) -> None:
        n_docs = max(1, len(self._raw))
        df: Counter[str] = Counter()
        for c in self._raw.values():
            df.update(c.keys())
        self._idf = {g: math.log(1 + n_docs / (1 + d)) for g, d in df.items()}
        for key, c in self._raw.items():
            vec = {g: cnt * self._idf.get(g, 0.0) for g, cnt in c.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self._vecs[key] = {g: v / norm for g, v in vec.items()}

    def search(self, query: str, *, allowed: set[str] | None = None, top_k: int = 20):
        qc = Counter(self._grams(query))
        qv = {g: cnt * self._idf.get(g, 0.0) for g, cnt in qc.items()}
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        qv = {g: v / qn for g, v in qv.items()}
        out = []
        for key, vec in self._vecs.items():
            if allowed is not None and key not in allowed:
                continue
            small, large = (qv, vec) if len(qv) < len(vec) else (vec, qv)
            s = sum(v * large.get(g, 0.0) for g, v in small.items())
            if s > 0:
                out.append((key, s))
        return sorted(out, key=lambda kv: (-kv[1], kv[0]))[:top_k]

    def _grams(self, text: str) -> list[str]:
        t = f" {normalize_alias(text) or text.casefold()} "
        return [t[i : i + self.n] for i in range(max(0, len(t) - self.n + 1))]


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
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.bm25 = Bm25Index()
        self.dense = DenseIndex()
        self._by_concept: dict[str, set[str]] = defaultdict(set)
        self._chunks: dict[str, Chunk] = {}
        for ch in kb.chunks:
            self._chunks[ch.chunk_id] = ch
            # 概念注入索引文本：让 BM25 也能命中"文中写 ORPATHYS、查询写沃利替尼"这类跨别名情形。
            enriched = ch.text + " " + " ".join(self._concept_terms(ch))
            self.bm25.add(ch.chunk_id, enriched)
            self.dense.add(ch.chunk_id, enriched)
            for cid in ch.concept_ids:
                self._by_concept[cid].add(ch.chunk_id)
        self.dense.build()

    def _concept_terms(self, chunk: Chunk) -> list[str]:
        terms = []
        for cid in chunk.concept_ids:
            c = self.kb.concept(cid)
            if c:
                terms.extend(filter(None, [c.preferred_label_en, c.preferred_label_zh]))
        return terms

    # -------------------------------------------------- 许可

    def _allowed_chunks(self, entitlements: frozenset[str], max_tier: LicenseTierEnum):
        allowed, filtered = set(), 0
        cap = tier_rank(max_tier)
        for ch in self.kb.chunks:
            doc = self.kb.document(ch.doc_id)
            if doc is None:
                continue
            t = tier_rank(doc.license_tier)
            if t > cap or (
                t > tier_rank(LicenseTierEnum.TIER_1) and doc.source_id not in entitlements
            ):
                filtered += 1
                continue
            allowed.add(ch.chunk_id)
        return allowed, filtered

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
    ) -> tuple[list[SearchHit], int]:
        ent = entitlements if entitlements is not None else ctx.entitlements
        allowed, filtered = self._allowed_chunks(ent, max_tier)
        if labels:
            wanted = set(labels)
            allowed = {c for c in allowed if wanted & set(self._chunks[c].labels)}

        results: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}
        with ctx.span("search", **{"hmd.query": query[:120], "hmd.allowed": len(allowed)}) as sp:
            concept_ids: list[str] = []
            if RetrievalChannelEnum.BM25 in channels:
                results[RetrievalChannelEnum.BM25] = self.bm25.search(
                    query, allowed=allowed, top_k=top_k * 3
                )
            if RetrievalChannelEnum.DENSE in channels:
                results[RetrievalChannelEnum.DENSE] = self.dense.search(
                    query, allowed=allowed, top_k=top_k * 3
                )
            if RetrievalChannelEnum.GRAPH in channels:
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
