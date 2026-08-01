"""Milvus 后端：三向量列 + 许可过滤下推 + 逐列可查。

**融合刻意不下推**。Milvus 自带 `RRFRanker` 更省事，但它返回的融合分
无法反解回各通道排名，`SearchHit.explain` 就废了 —— 那正是四支柱里
WHY 这一支的载体。所以这里只返回各列的 `(chunk_id, score)`，融合仍在进程内做。

许可过滤走**标量下推 + partition key 双保险**：
- `expr` 让无权调用方的查询根本不返回受限行；
- `partition_key_field="source_id"` 让采购边界成为物理边界，
  即使表达式哪天写错，付费分区也不会被无凭据查询触碰。
"""

from __future__ import annotations

import re
from typing import Any

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.embed import Embedder, FakeEmbedder
from biomed_ontology.search.backends.base import (
    BackendResult,
    ChunkMeta,
    RetrievalRequest,
)

__all__ = ["MilvusBackend", "collection_schema"]

# 列 → 检索通道。稀疏列对应 BM25 通道（两者都是词法匹配），
# 两条稠密列共用 DENSE 通道但可分别启用，消融靠 vector_fields 控制。
_CHANNEL = {
    "sparse_lexical": RetrievalChannelEnum.BM25,
    "dense_general": RetrievalChannelEnum.DENSE,
    "dense_biomed": RetrievalChannelEnum.DENSE,
}

_METRIC = {
    "sparse_lexical": "IP",
    "dense_general": "COSINE",
    "dense_biomed": "COSINE",
}

_INDEX = {
    "sparse_lexical": {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
    "dense_general": {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200},
    },
    "dense_biomed": {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200},
    },
}


def collection_schema(client: Any, *, dims: dict[str, int]) -> Any:
    from pymilvus import DataType

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
    # 采购边界即物理边界：无凭据查询不会触碰付费来源的分区
    schema.add_field("source_id", DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field("license_rank", DataType.INT8)
    schema.add_field("section_id", DataType.VARCHAR, max_length=160)
    schema.add_field("section_path", DataType.VARCHAR, max_length=1024)
    schema.add_field("sort_order", DataType.INT32)
    schema.add_field("page", DataType.INT32)
    schema.add_field("modality", DataType.VARCHAR, max_length=32)
    schema.add_field("degraded", DataType.VARCHAR, max_length=256)
    schema.add_field(
        "labels", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=32, max_length=64
    )
    schema.add_field(
        "concept_ids_expanded",
        DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=128,
        max_length=64,
    )
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("dense_general", DataType.FLOAT_VECTOR, dim=dims["dense_general"])
    schema.add_field("sparse_lexical", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("dense_biomed", DataType.FLOAT_VECTOR, dim=dims["dense_biomed"])
    return schema


class MilvusBackend:
    name = "milvus"

    def __init__(
        self,
        *,
        uri: str = "http://localhost:19530",
        token: str = "",
        collection: str = "hmd_chunks",
        embedder: Embedder | None = None,
        known_sources: frozenset[str] | None = None,
        client: Any = None,
    ) -> None:
        self.collection = collection
        self.embedder = embedder or FakeEmbedder()
        self.known_sources = known_sources
        self._client = client
        self._uri = uri
        self._token = token

    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self._uri, token=self._token)
        return self._client

    # ------------------------------------------------------------- 建表与写入

    def ensure_collection(self, *, drop_existing: bool = False) -> None:
        if drop_existing and self.client.has_collection(self.collection):
            self.client.drop_collection(self.collection)
        if self.client.has_collection(self.collection):
            return

        dims = {
            "dense_general": self.embedder.dims.get("dense_general", 1024),
            "dense_biomed": self.embedder.dims.get("dense_biomed", 768),
        }
        schema = collection_schema(self.client, dims=dims)
        index = self.client.prepare_index_params()
        for field, spec in _INDEX.items():
            index.add_index(field_name=field, **spec)
        self.client.create_collection(
            collection_name=self.collection, schema=schema, index_params=index
        )

    def upsert(self, rows: list[dict[str, Any]]) -> int:
        """`rows` 需含元数据与 `text`；向量在此处算，调用方不必知道模型。"""
        if not rows:
            return 0
        bundles = self.embedder.encode([str(r["text"]) for r in rows])
        payload = [{**row, **bundle} for row, bundle in zip(rows, bundles, strict=True)]
        self.client.upsert(collection_name=self.collection, data=payload)
        return len(payload)

    # ----------------------------------------------------------------- 检索

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        fields = request.vector_fields or ("sparse_lexical", "dense_general", "dense_biomed")
        expr = self._filter(request)
        depth = request.top_k * 3

        bundle = self.embedder.encode([request.query])[0]
        channels: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}

        for field in fields:
            vector = bundle.get(field)
            if vector is None:
                continue  # 该列没算 —— 补零会让"没算"和"算出来是零"分不清
            hits = self.client.search(
                collection_name=self.collection,
                data=[vector],
                anns_field=field,
                filter=expr,
                limit=depth,
                output_fields=["chunk_id"],
                search_params={"metric_type": _METRIC[field]},
            )
            scored = [(h["entity"]["chunk_id"], float(h["distance"])) for h in hits[0]]
            channel = _CHANNEL[field]
            channels.setdefault(channel, [])
            channels[channel] = _merge_best(channels[channel], scored)

        return BackendResult(channels=channels, filtered_count=self._filtered_count(request, expr))

    def restore_section(
        self, doc_id: str, section_id: str, request: RetrievalRequest
    ) -> list[dict]:
        """按章节拉全，供 Citationware 还原原文。许可谓词一并生效。

        `doc_id` / `section_id` 来自调用方传入的引用，必须先验形状 ——
        直接拼字符串就是把许可边界交给了调用方。
        """
        ident = f'doc_id == "{_safe_ident(doc_id)}" and section_id == "{_safe_ident(section_id)}"'
        rows = self.client.query(
            collection_name=self.collection,
            filter=f"{ident} and {self._filter(request)}",
            output_fields=["chunk_id", "text", "sort_order", "page", "section_path"],
            limit=1000,
        )
        return sorted(rows, key=lambda r: int(r.get("sort_order", 0)))

    # ----------------------------------------------------------------- 内部

    def _filter(self, request: RetrievalRequest) -> str:
        expr = request.scope.milvus_expr(known_sources=self.known_sources)
        if request.labels:
            allow = ", ".join(f'"{lbl}"' for lbl in request.labels if _plain(lbl))
            if allow:
                expr = f"{expr} and ARRAY_CONTAINS_ANY(labels, [{allow}])"
        return expr

    def _filtered_count(self, request: RetrievalRequest, expr: str) -> int:
        """被挡掉多少条。无权调用方看到 0 命中时，这个数字是唯一的线索。"""
        total = self.client.query(
            collection_name=self.collection, filter="", output_fields=["count(*)"]
        )
        allowed = self.client.query(
            collection_name=self.collection, filter=expr, output_fields=["count(*)"]
        )
        return max(0, _count(total) - _count(allowed))


def _count(rows: Any) -> int:
    try:
        return int(rows[0]["count(*)"])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


_IDENT_OK = re.compile(r"^[A-Za-z0-9_.:#/-]+$")


def _safe_ident(value: str) -> str:
    if not _IDENT_OK.match(value or ""):
        raise ValueError(f"标识符含非法字符，拒绝拼入过滤表达式：{value!r}")
    return value


def _plain(value: str) -> bool:
    return value.replace("_", "").replace("-", "").isalnum()


def _merge_best(
    existing: list[tuple[str, float]], incoming: list[tuple[str, float]]
) -> list[tuple[str, float]]:
    """同一通道下多列的结果取每个 chunk 的最高分，再按分排序。"""
    best: dict[str, float] = dict(existing)
    for cid, score in incoming:
        if score > best.get(cid, float("-inf")):
            best[cid] = score
    return sorted(best.items(), key=lambda kv: -kv[1])


def chunk_to_row(chunk: Any, meta: ChunkMeta, *, degraded: str = "") -> dict[str, Any]:
    return {
        "chunk_id": meta.chunk_id,
        "doc_id": meta.doc_id,
        "source_id": meta.source_id,
        "license_rank": meta.license_rank,
        "section_id": getattr(chunk, "section_id", "") or "",
        "section_path": getattr(chunk, "section", "") or "",
        "sort_order": int(getattr(chunk, "char_start", 0)),
        "page": int(getattr(chunk, "page", 1)),
        "modality": str(getattr(chunk.modality, "value", chunk.modality)),
        "degraded": degraded,
        "labels": list(meta.labels),
        "concept_ids_expanded": list(getattr(chunk, "concept_ids_expanded", ())),
        "text": chunk.text,
    }
