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
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.embed import Embedder, FakeEmbedder
from biomed_ontology.parse.assets import resolve_asset
from biomed_ontology.search.backends.base import (
    BackendResult,
    ChunkMeta,
    RetrievalRequest,
    merge_best,
)

__all__ = ["MilvusBackend", "collection_schema"]

# 列 → 检索通道。稀疏列对应 BM25 通道（两者都是词法匹配），
# 四条稠密列共用 DENSE 通道但可分别启用，消融靠 vector_fields 控制。
_CHANNEL = {
    "sparse_lexical": RetrievalChannelEnum.BM25,
    "dense_general": RetrievalChannelEnum.DENSE,
    "dense_biomed": RetrievalChannelEnum.DENSE,
    "dense_visual": RetrievalChannelEnum.DENSE,
    "dense_visual_bio": RetrievalChannelEnum.DENSE,
}

_HNSW = {
    "index_type": "HNSW",
    "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200},
}

_METRIC = {
    "sparse_lexical": "IP",
    "dense_general": "COSINE",
    "dense_biomed": "COSINE",
    "dense_visual": "COSINE",
    "dense_visual_bio": "COSINE",
}

_INDEX = {
    "sparse_lexical": {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
    "dense_general": dict(_HNSW),
    "dense_biomed": dict(_HNSW),
    "dense_visual": dict(_HNSW),
    "dense_visual_bio": dict(_HNSW),
}

DEFAULT_DIMS = {
    "dense_general": 1024,
    "dense_biomed": 768,
    "dense_visual": 2048,
    "dense_visual_bio": 512,
}

_DENSE_COLUMNS = ("dense_general", "dense_biomed", "dense_visual", "dense_visual_bio")


def collection_schema(
    client: Any, *, dims: dict[str, int], sparse: bool = True, description: str = ""
) -> Any:
    from pymilvus import DataType

    schema = client.create_schema(
        auto_id=False, enable_dynamic_field=False, description=description
    )
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
    # 图块像素的落盘位置。命中视觉列时要能把原图摆回给人看 ——
    # 一个只能给出向量得分、拿不出图的命中，在审计场景下不算证据。
    # 给默认值而非必填：绝大多数切片是纯文本，本就没有图。
    schema.add_field("asset_path", DataType.VARCHAR, max_length=512, default_value="")
    # 图型（RADIOLOGY / MICROSCOPY / CHART / ...）。落成标量而不是靠向量相似度碰运气：
    # 「我要看那张 CT」是个布尔条件，和 `modality` 同一性质 —— 能下推就不该去猜。
    # 没打过标的切片是 ""，语义是"未分类"，不是"不是图"。
    schema.add_field("figure_type", DataType.VARCHAR, max_length=32, default_value="")
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
    # 只建 embedder 真的会写的列。向量列不可为空，多建一列的下场是整批 upsert 全挂。
    for name in _DENSE_COLUMNS:
        if name in dims:
            schema.add_field(name, DataType.FLOAT_VECTOR, dim=dims[name])
    if sparse:
        schema.add_field("sparse_lexical", DataType.SPARSE_FLOAT_VECTOR)
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
        asset_root: Path | None = None,
        client: Any = None,
    ) -> None:
        self.collection = collection
        self.embedder = embedder or FakeEmbedder()
        self.known_sources = known_sources
        # 切片里存的是相对路径（collection 才能跨机器搬），读像素时在这里拼回绝对路径。
        self.asset_root = asset_root
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
            name: int(self.embedder.dims.get(name, default))
            for name, default in DEFAULT_DIMS.items()
        }
        # 拿一次真前向问 embedder"你到底出哪几列"，而不是靠登记表。
        # 登记表会和实现漂移，而 schema 与写入对不上时只会在批量写入时才爆。
        emitted = set(self.embedder.encode(["probe"])[0])
        dims = {k: v for k, v in dims.items() if k in emitted}
        sparse = "sparse_lexical" in emitted
        # 把建表用的 embedder 刻进集合描述。用 A 模型写入、用 B 模型检索不会报错，
        # 只会给出一批看上去很正常、实际毫无意义的分数 —— 那是最难发现的错。
        schema = collection_schema(
            self.client, dims=dims, sparse=sparse, description=f"embedder={self.embedder.name}"
        )
        index = self.client.prepare_index_params()
        for field, spec in _INDEX.items():
            if field in dims or (field == "sparse_lexical" and sparse):
                index.add_index(field_name=field, **spec)
        try:
            self.client.create_collection(
                collection_name=self.collection, schema=schema, index_params=index
            )
        except Exception as exc:
            _explain_vector_field_cap(exc, len(dims) + int(sparse))
            raise

    def _asset(self, rel_path: Any, doc_id: Any = None) -> str | None:
        """切片存的相对路径 → 本机绝对路径。图不在就返回 None（退化成纯文本编码）。"""
        return resolve_asset(self.asset_root, doc_id, rel_path)

    def stamped_embedder(self) -> str:
        """建这张表时用的 embedder 名。集合不存在或没刻名字则返回空串。"""
        if not self.client.has_collection(self.collection):
            return ""
        desc = str(self.client.describe_collection(self.collection).get("description", ""))
        return desc.removeprefix("embedder=") if desc.startswith("embedder=") else ""

    def vector_fields(self) -> tuple[str, ...]:
        """集合里真实存在的向量列。以库为准，不以代码里的常量为准。"""
        info = self.client.describe_collection(self.collection)
        names = {f["name"] for f in info.get("fields", ())}
        return tuple(f for f in _METRIC if f in names)

    def upsert(self, rows: list[dict[str, Any]], *, flush: bool = True) -> int:
        """`rows` 需含元数据与 `text`；向量在此处算，调用方不必知道模型。

        默认 flush：Milvus 的默认一致性是 Bounded，不 flush 时刚写的数据查不到，
        而失败形态是“检索返回空” —— 看起来像召回差，不像数据没进去。
        分批写入时传 `flush=False`，最后自行调一次。
        """
        if not rows:
            return 0
        bundles = self.embedder.encode(
            [str(r["text"]) for r in rows],
            images=[self._asset(r.get("asset_path"), r.get("doc_id")) for r in rows],
        )
        payload = [{**row, **bundle} for row, bundle in zip(rows, bundles, strict=True)]
        self.client.upsert(collection_name=self.collection, data=payload)
        if flush:
            self.client.flush(self.collection)
        return len(payload)

    # ----------------------------------------------------------------- 检索

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        # 默认查集合里真实存在的列。写死一份清单的话，用 4 列建的表遇上
        # 5 列的默认值就会在一个不存在的字段上搜 —— 报错还算好的，
        # 更糟的是有人为了让它别报错而把默认值改窄，于是新列悄悄不参与检索了。
        fields = request.vector_fields or self.vector_fields()
        expr = self._filter(request)
        depth = request.top_k * 3

        # 词法串与稠密串可能不同（本体改写后），但去重后一次前向编完 ——
        # BGE-M3 一次就同时给出稠密与稀疏两种表示，按串分开调是白付一倍算力。
        lexical_text = request.lexical_text()
        dense_texts = request.dense_texts()
        texts = list(dict.fromkeys([lexical_text, *dense_texts]))
        bundles = dict(zip(texts, self.embedder.encode(texts), strict=True))
        channels: dict[RetrievalChannelEnum, list[tuple[str, float]]] = {}

        for field in fields:
            channel = _CHANNEL[field]
            queries = (lexical_text,) if channel is RetrievalChannelEnum.BM25 else dense_texts
            for text in queries:
                vector = bundles[text].get(field)
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
                channels.setdefault(channel, [])
                channels[channel] = merge_best(channels[channel], scored)

        # 计数只按许可谓词算，不带 labels / modality：后两者是调用方自己下的条件。
        # 混进来会让 `license_filtered_count` 在"你无权查看"与"你自己筛掉的"之间摇摆，
        # 而本地后端算的一直是前者 —— 两个后端对同一字段给出不同含义是最坏的情形。
        return BackendResult(
            channels=channels,
            filtered_count=self._filtered_count(request, self._license_expr(request)),
        )

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

    def _license_expr(self, request: RetrievalRequest) -> str:
        return request.scope.milvus_expr(known_sources=self.known_sources)

    def _filter(self, request: RetrievalRequest) -> str:
        expr = self._license_expr(request)
        if request.labels:
            allow = ", ".join(f'"{lbl}"' for lbl in request.labels if _plain(lbl))
            if allow:
                expr = f"{expr} and ARRAY_CONTAINS_ANY(labels, [{allow}])"
        if request.modalities:
            # 下推而非取回后再筛：模态过滤后候选可能只剩几十条，
            # 在库外筛意味着 limit 先砍在混排结果上，图根本进不了这一批。
            mods = ", ".join(f'"{m}"' for m in request.modalities if _plain(m))
            if mods:
                expr = f"{expr} and modality in [{mods}]"
        if request.figure_types:
            # 与 modality 同一条路：`modalities=[IMAGE]` 只保证返回的是图，
            # 不保证是**那类**图（README 里那个 CT 查询返回了一张信号强度柱状图）。
            # 图型是缩小这个空间的下一格。
            kinds = ", ".join(f'"{f}"' for f in request.figure_types if _plain(f))
            if kinds:
                expr = f"{expr} and figure_type in [{kinds}]"
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


def _explain_vector_field_cap(exc: Exception, wanted: int) -> None:
    """把 Milvus 的向量列数上限翻译成"去改哪个配置"。

    原始报错是 "maximum vector field's number should be limited to 4"，
    它不说这是**服务端配置**而不是 schema 写错了，于是最省事的"修法"看起来
    就是砍掉一列 —— 那会让第五列静默消失，而报表上没有任何痕迹。
    """
    if "vector field" not in str(exc):
        return
    print(
        f"[milvus] 本次要建 {wanted} 个向量列，超过服务端上限。"
        "这是 Milvus 的 proxy.maxVectorFieldNum（默认 4，上限 10），不是 schema 的问题。"
        "docker/milvus-standalone.yml 里已设 PROXY_MAXVECTORFIELDNUM=6，"
        "请 make milvus-down && make milvus-up 让它生效。"
        "**不要靠删掉一列来绕过** —— 那一列会从报表上无声消失。",
        flush=True,
    )


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
        "asset_path": getattr(chunk, "asset_path", None) or "",
        "figure_type": str(getattr(chunk, "figure_type", "") or ""),
        "labels": list(meta.labels),
        "concept_ids_expanded": list(getattr(chunk, "concept_ids_expanded", ())),
        "text": chunk.text,
    }
