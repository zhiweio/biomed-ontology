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
from pathlib import Path
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

# HF 仓库 ID → ModelScope 仓库 ID。两边的命名空间是独立的，没有换算规则，只能逐条列。
_MODELSCOPE_IDS = {
    "BAAI/bge-m3": "BAAI/bge-m3",
    "cambridgeltl/SapBERT-from-PubMedBERT-fulltext": "Xenova/SapBERT-from-PubMedBERT-fulltext",
}


def resolve_model(model_id: str) -> str:
    """把 HF 仓库 ID 换成可直接喂给 transformers 的路径。

    `hub=hf` 时原样返回，由下游自己去 huggingface.co 拿；
    `hub=modelscope` 时先下载快照再返回本地目录 —— 下不动就报错，
    不回落到 HF，否则内网环境下会卡在一次必然失败的超时上。
    """
    from biomed_ontology.config import settings

    if settings.model_hub != "modelscope":
        return model_id
    try:
        target = _MODELSCOPE_IDS[model_id]
    except KeyError:
        raise ValueError(
            f"{model_id} 没有登记 ModelScope 对应仓库，请补进 embed._MODELSCOPE_IDS"
        ) from None

    from modelscope import snapshot_download

    return snapshot_download(target, cache_dir=str(settings.model_cache_dir))


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

    def __init__(
        self,
        *,
        model_id: str = "BAAI/bge-m3",
        device: str = "cpu",
        use_fp16: bool = False,
    ) -> None:
        from pymilvus.model.hybrid import BGEM3EmbeddingFunction

        self._fn = BGEM3EmbeddingFunction(
            model_name=resolve_model(model_id), use_fp16=use_fp16, device=device
        )
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

    权重格式按目录里实际有什么来定：ModelScope 上只有 ONNX 版（Xenova），
    HF 上是 PyTorch 版。两条路都取 `[CLS]` 再 L2 归一 —— 这是 SapBERT 的
    规定取法，换成 mean pooling 会得到另一个模型的向量。
    """

    name = "sapbert"

    def __init__(
        self,
        *,
        model_id: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        device: str = "cpu",
    ) -> None:
        path = resolve_model(model_id)
        onnx = Path(path) / "onnx" / "model.onnx"
        self._onnx = onnx.is_file()
        if self._onnx:
            import onnxruntime
            from transformers import AutoTokenizer

            self._tok = AutoTokenizer.from_pretrained(path)
            self._sess = onnxruntime.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
            self._inputs = {i.name for i in self._sess.get_inputs()}
            dim = int(self._sess.get_outputs()[0].shape[-1])
        else:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(path, device=device)
            dim = int(self._model.get_sentence_embedding_dimension())
        self.dims = {"dense_biomed": dim}

    def _encode_onnx(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        toks = self._tok(texts, padding=True, truncation=True, max_length=256, return_tensors="np")
        feed = {k: v for k, v in toks.items() if k in self._inputs}
        cls = self._sess.run(None, feed)[0][:, 0, :]
        norm = np.linalg.norm(cls, axis=1, keepdims=True)
        return (cls / np.where(norm == 0, 1, norm)).tolist()

    def encode(self, texts: list[str]) -> list[EmbeddingBundle]:
        if self._onnx:
            return [EmbeddingBundle(dense_biomed=v) for v in self._encode_onnx(texts)]
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
