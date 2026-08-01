"""向量化：双塔 + 稀疏词法，外加一个确定性假实现供 CI。

三路各司其职：
- `dense_general`  BGE-M3 稠密 1024 维 —— 通用语义，中英都行
- `sparse_lexical` BGE-M3 词法稀疏 —— 精确术语（"MET exon 14"）不会被语义抹平
- `dense_biomed`   SapBERT 768 维 —— 生物医药实体对齐，**英文强、中文弱**

最后一条是 P13 要按语种拆开报告的原因：总平均会把"英文涨了、中文没动"抹平。

`FakeEmbedder` 不是占位符，是 CI 的一等公民：真模型要下载 GB 级权重，
把它拖进测试会让每次跑测试都变成一场赌博。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

__all__ = [
    "VECTOR_FIELDS",
    "BiomedEmbedder",
    "Embedder",
    "EmbeddingBundle",
    "FakeEmbedder",
    "GeneralEmbedder",
    "get_embedder",
]

VECTOR_FIELDS = ("dense_general", "sparse_lexical", "dense_biomed")

_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


class EmbeddingBundle(dict[str, object]):
    """一次前向产出的全部向量列。键是 Milvus 字段名，不另起别名。"""


@runtime_checkable
class Embedder(Protocol):
    name: str
    dims: dict[str, int]

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]: ...


class FakeEmbedder:
    """确定性哈希向量。同样的文本永远给同样的向量，跨进程、跨机器一致。

    它测不出语义质量 —— 那是 P13 的十臂消融要回答的问题。
    它测的是**管线接线是否正确**：过滤有没有生效、三列能不能分别查、
    许可有没有泄漏。这些和模型好坏无关，却最容易写错。
    """

    name = "fake"

    def __init__(self, *, general_dim: int = 64, biomed_dim: int = 32) -> None:
        self.dims = {"dense_general": general_dim, "dense_biomed": biomed_dim}

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]:
        return [
            EmbeddingBundle(
                dense_general=_hash_vector(t, self.dims["dense_general"], salt="general"),
                dense_biomed=_hash_vector(t, self.dims["dense_biomed"], salt="biomed"),
                sparse_lexical=_hash_sparse(t),
            )
            for t in texts
        ]


def _hash_vector(text: str, dim: int, *, salt: str) -> list[float]:
    """词袋 → 定长稠密向量。相似文本共享词，因此向量也相近。"""
    vec = [0.0] * dim
    for token in _TOKEN.findall(text.casefold()) or ["\x00"]:
        digest = hashlib.blake2b(f"{salt}:{token}".encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _hash_sparse(text: str) -> dict[int, float]:
    """词法稀疏向量：词 → 词频。维度靠哈希定，与真 BGE-M3 的接口形状一致。"""
    counts: dict[int, float] = {}
    for token in _TOKEN.findall(text.casefold()):
        idx = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big") % 65536
        counts[idx] = counts.get(idx, 0.0) + 1.0
    total = sum(counts.values()) or 1.0
    return {k: v / total for k, v in counts.items()}


class GeneralEmbedder:
    """BGE-M3：一次前向同时给出稠密与词法稀疏，省一半算力。"""

    name = "bge-m3"

    def __init__(self, *, device: str = "cpu", use_fp16: bool = False) -> None:
        from pymilvus.model.hybrid import BGEM3EmbeddingFunction

        self._fn = BGEM3EmbeddingFunction(use_fp16=use_fp16, device=device)
        self.dims = {"dense_general": int(self._fn.dim["dense"])}

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]:
        out = self._fn.encode_documents(texts)
        dense, sparse = out["dense"], out["sparse"]
        return [
            EmbeddingBundle(
                dense_general=list(dense[i]),
                sparse_lexical=_row_to_dict(sparse, i),
            )
            for i in range(len(texts))
        ]


class BiomedEmbedder:
    """SapBERT：UMLS 同义词对齐训练，把"savolitinib"与"AZD6094"拉到一起。

    **英文单语**。中文专利上它大概率无增益甚至有害 —— 所以 P13 给它单独一臂，
    并按语种分表；如果 zh 为负，就按语种路由向量列而不是一刀切。
    """

    name = "sapbert"

    def __init__(
        self,
        *,
        model_id: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        device: str = "cpu",
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_id, device=device)
        self.dims = {"dense_biomed": int(self._model.get_sentence_embedding_dimension())}

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [EmbeddingBundle(dense_biomed=list(map(float, v))) for v in vecs]


class CompositeEmbedder:
    """把多个 embedder 的产出并成一个 bundle。列缺失就是缺失，不补零。

    补零会让"这一列没算"和"这一列算出来是零向量"分不清，
    而后者在余弦距离下是未定义的。
    """

    name = "composite"

    def __init__(self, *parts: Embedder) -> None:
        self.parts = parts
        self.dims = {k: v for p in parts for k, v in p.dims.items()}

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]:
        merged = [EmbeddingBundle() for _ in texts]
        for part in self.parts:
            for i, bundle in enumerate(part.encode(texts)):
                merged[i].update(bundle)
        return merged


def _row_to_dict(matrix: object, row: int) -> dict[int, float]:
    csr = matrix[[row]]  # type: ignore[index]
    coo = csr.tocoo()
    return {int(c): float(v) for c, v in zip(coo.col, coo.data, strict=True)}


def get_embedder(name: str = "fake", *, device: str = "cpu") -> Embedder:
    """配置开关的唯一落点。默认 fake —— 不下模型也能跑通全链路。"""
    if name == "fake":
        return FakeEmbedder()
    if name == "bge-m3":
        return GeneralEmbedder(device=device)
    if name == "sapbert":
        return BiomedEmbedder(device=device)
    if name == "dual":
        return CompositeEmbedder(GeneralEmbedder(device=device), BiomedEmbedder(device=device))
    raise ValueError(f"未知 embedder：{name!r}")
