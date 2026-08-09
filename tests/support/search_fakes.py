"""检索单测替身：Seed 邻域 + stub SearchBackend。"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.alias import normalize_alias
from biomed_ontology.ontology.links import Neighbor, walk_neighbors
from biomed_ontology.search.backends.base import BackendResult, ChunkMeta, RetrievalRequest

__all__ = [
    "RecordingBackend",
    "SeedNeighborhood",
    "StaticBackend",
    "TokenOverlapBackend",
    "make_searcher",
    "seed_neighborhood",
]

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    return [normalize_alias(t) or t.casefold() for t in _TOKEN.findall(text or "")]


class SeedNeighborhood:
    """从 BuiltConcept.parents / links 建邻接，供离线测 walk / GRAPH（非生产路径）。"""

    def __init__(self, concepts: list[Any]) -> None:
        edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_id = {c.concept_id: c for c in concepts}
        for c in concepts:
            for parent in c.parents:
                if parent in by_id:
                    edges[c.concept_id].append((parent, "broader"))
                    edges[parent].append((c.concept_id, "narrower"))
            for link in c.links:
                edges[c.concept_id].append((link.object_id, link.predicate))
                inv = {"has_target": "targeted_by", "treats": "treated_by"}.get(link.predicate)
                if inv:
                    edges[link.object_id].append((c.concept_id, inv))
        self._edges = dict(edges)

    def neighbors(
        self,
        seeds: list[str] | set[str],
        *,
        max_hops: int = 2,
        predicates: frozenset[str] | None = None,
        min_weight: float = 0.1,
    ) -> list[Neighbor]:
        return walk_neighbors(
            seeds,
            lambda cids: {c: self._edges.get(c, []) for c in cids},
            max_hops=max_hops,
            predicates=predicates,
            min_weight=min_weight,
        )


def seed_neighborhood(kb: Any) -> SeedNeighborhood:
    return SeedNeighborhood(kb.concepts)


@dataclass
class StaticBackend:
    """按许可/模态过滤后返回预置命中；用于 HybridSearcher 编排单测。"""

    name: str = "static"
    rows: list[tuple[ChunkMeta, list[tuple[RetrievalChannelEnum, float]]]] = field(
        default_factory=list
    )
    last_request: RetrievalRequest | None = None

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        self.last_request = request
        channels: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}
        filtered = 0
        wanted_labels = set(request.labels)
        wanted_mod = set(request.modalities)
        wanted_ft = set(request.figure_types)
        for meta, scored in self.rows:
            if not request.scope.permits(meta.license_rank, meta.source_id):
                filtered += 1
                continue
            if wanted_labels and not wanted_labels & set(meta.labels):
                continue
            if wanted_mod and meta.modality not in wanted_mod:
                continue
            if wanted_ft and meta.figure_type not in wanted_ft:
                continue
            for channel, score in scored:
                if channel not in request.channels:
                    continue
                channels.setdefault(channel, []).append((meta.chunk_id, score))
        return BackendResult(channels=channels, filtered_count=filtered)


@dataclass
class RecordingBackend:
    """记录 retrieve 调用；可选委托给内层 backend。"""

    name: str = "recording"
    inner: Any = None
    calls: list[RetrievalRequest] = field(default_factory=list)

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        self.calls.append(request)
        if self.inner is not None:
            return self.inner.retrieve(request)
        return BackendResult(channels={}, filtered_count=0)


@dataclass
class TokenOverlapBackend:
    """离线编排用的 Okapi 风格词法 + 字符 3-gram 稠密（仅 tests，非评测采购基线）。"""

    name: str = "token-overlap"
    docs: dict[str, tuple[ChunkMeta, str]] = field(default_factory=dict)
    _tf: dict[str, Counter[str]] = field(default_factory=dict)
    _len: dict[str, int] = field(default_factory=dict)
    _df: Counter[str] = field(default_factory=Counter)
    _avgdl: float = 0.0
    _grams: dict[str, Counter[str]] = field(default_factory=dict)
    _gdf: Counter[str] = field(default_factory=Counter)
    k1: float = 1.5
    b: float = 0.75

    def add(self, meta: ChunkMeta, text: str) -> None:
        self.docs[meta.chunk_id] = (meta, text)
        tf = Counter(_tokens(text))
        self._tf[meta.chunk_id] = tf
        self._len[meta.chunk_id] = sum(tf.values())
        for term in tf:
            self._df[term] += 1
        grams = Counter(text[i : i + 3] for i in range(max(0, len(text) - 2)))
        self._grams[meta.chunk_id] = grams
        for g in grams:
            self._gdf[g] += 1
        self._avgdl = sum(self._len.values()) / max(1, len(self._len))

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        n = len(self.docs) or 1
        q_terms = _tokens(request.lexical_text())
        dense_qs = list(request.dense_texts())
        channels: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}
        filtered = 0
        wanted_labels = set(request.labels)
        wanted_mod = set(request.modalities)
        wanted_ft = set(request.figure_types)
        bm25: list[tuple[str, float]] = []
        dense: list[tuple[str, float]] = []
        for cid, (meta, text) in self.docs.items():
            if not request.scope.permits(meta.license_rank, meta.source_id):
                filtered += 1
                continue
            if wanted_labels and not wanted_labels & set(meta.labels):
                continue
            if wanted_mod and meta.modality not in wanted_mod:
                continue
            if wanted_ft and meta.figure_type not in wanted_ft:
                continue
            if RetrievalChannelEnum.BM25 in request.channels and q_terms:
                score = 0.0
                tf = self._tf.get(cid, Counter())
                dl = self._len.get(cid, 0)
                for term in q_terms:
                    df = self._df.get(term, 0)
                    if not df:
                        continue
                    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                    f = tf.get(term, 0)
                    if not f:
                        continue
                    denom = f + self.k1 * (1 - self.b + self.b * dl / max(1e-9, self._avgdl))
                    score += idf * (f * (self.k1 + 1)) / denom
                if score:
                    bm25.append((cid, score))
            if RetrievalChannelEnum.DENSE in request.channels and dense_qs:
                best = 0.0
                doc_g = self._grams.get(cid, Counter())
                for dq in dense_qs:
                    qg = Counter(dq[i : i + 3] for i in range(max(0, len(dq) - 2)))
                    if not qg or not doc_g:
                        continue
                    # 加权余弦：idf 压高频 3-gram
                    num = 0.0
                    qn = 0.0
                    dn = 0.0
                    for g, qf in qg.items():
                        idf = math.log(1 + n / max(1, self._gdf.get(g, 1)))
                        qw = qf * idf
                        dw = doc_g.get(g, 0) * idf
                        num += qw * dw
                        qn += qw * qw
                    for g, df in doc_g.items():
                        idf = math.log(1 + n / max(1, self._gdf.get(g, 1)))
                        dw = df * idf
                        dn += dw * dw
                    if qn and dn:
                        best = max(best, num / math.sqrt(qn * dn))
                if best:
                    dense.append((cid, best))
        if bm25:
            channels[RetrievalChannelEnum.BM25] = sorted(bm25, key=lambda x: (-x[1], x[0]))[
                : request.top_k * 3
            ]
        if dense:
            channels[RetrievalChannelEnum.DENSE] = sorted(dense, key=lambda x: (-x[1], x[0]))[
                : request.top_k * 3
            ]
        return BackendResult(channels=channels, filtered_count=filtered)


def make_searcher(kb: Any):
    """离线 HybridSearcher：TokenOverlap + SeedNeighborhood。"""
    from biomed_ontology.search import HybridSearcher

    backend = TokenOverlapBackend()
    searcher = HybridSearcher(kb, backend=backend, neighborhood=seed_neighborhood(kb))
    for ch in kb.chunks:
        meta = searcher.chunk_meta(ch.chunk_id)
        assert meta is not None
        labels = " ".join(searcher.concept_label_terms(ch))
        backend.add(meta, f"{ch.text} {labels}".strip())
    return searcher
