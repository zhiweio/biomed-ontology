"""本地检索后端：内存 BM25 + 字符 3-gram TF-IDF，零外部依赖。

存在的意义不只是"Milvus 装不上时的备胎"：评测需要一个完全可复现、
不受索引状态与模型版本漂移影响的基线，否则"本体带来多少增量"这个核心问题
会被"换个 embedding 会不会更好"淹没。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.alias import normalize_alias
from biomed_ontology.search.backends.base import (
    BackendResult,
    ChunkMeta,
    RetrievalRequest,
)

__all__ = ["Bm25Index", "DenseIndex", "LocalBackend"]

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    return [normalize_alias(t) or t.casefold() for t in _TOKEN.findall(text)]


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

    生产替换点是 Milvus 后端的真实向量列，接口不变。
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


class LocalBackend:
    name = "local"

    def __init__(self) -> None:
        self.bm25 = Bm25Index()
        self.dense = DenseIndex()
        self._meta: dict[str, ChunkMeta] = {}

    def add(self, meta: ChunkMeta, text: str) -> None:
        self._meta[meta.chunk_id] = meta
        self.bm25.add(meta.chunk_id, text)
        self.dense.add(meta.chunk_id, text)

    def build(self) -> None:
        self.dense.build()

    def allow_list(self, request: RetrievalRequest) -> tuple[set[str], int]:
        """许可与标签过滤在**候选生成阶段**介入，而非返回前裁剪。

        后者会让"总命中数"这类统计量泄漏无权数据的存在性。
        """
        wanted = set(request.labels)
        allowed: set[str] = set()
        filtered = 0
        for meta in self._meta.values():
            if not request.scope.permits(meta.license_rank, meta.source_id):
                filtered += 1
                continue
            if wanted and not wanted & set(meta.labels):
                continue
            allowed.add(meta.chunk_id)
        return allowed, filtered

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        allowed, filtered = self.allow_list(request)
        out = BackendResult(filtered_count=filtered)
        depth = request.top_k * 3
        if RetrievalChannelEnum.BM25 in request.channels:
            out.channels[RetrievalChannelEnum.BM25] = self.bm25.search(
                request.query, allowed=allowed, top_k=depth
            )
        if RetrievalChannelEnum.DENSE in request.channels:
            out.channels[RetrievalChannelEnum.DENSE] = self.dense.search(
                request.query, allowed=allowed, top_k=depth
            )
        return out
