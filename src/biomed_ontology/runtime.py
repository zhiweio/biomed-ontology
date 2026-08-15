"""双面运行时装配：ToolApi（文献）+ FoundationApi（World Model）。

运行时入口是 ``open_dual_surface()``。文献检索只认 Milvus + GraphDB 邻域；
身份 / 扩展经 Foundation ER + GraphDB。Citationware 正文走 ChunkStore。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DualSurface",
    "attach_public_assist",
    "build_literature_searcher",
    "open_dual_surface",
]


@dataclass
class DualSurface:
    """KB 文献面 + World Model 面的统一句柄。"""

    tools: Any  # ToolApi
    foundation: Any  # FoundationApi
    kb: Any | None = None
    search_backend: str = "milvus"
    chunk_store: Any | None = None
    identity: Any | None = None  # IdentityService

    @property
    def world(self) -> Any:
        return self.foundation.world


def build_literature_searcher(
    kb: Any,
    *,
    milvus_backend: Any,
    neighborhood: Any | None = None,
    ensure_graph: bool = True,
    chunk_store: Any | None = None,
) -> Any:
    """装配 HybridSearcher：Milvus 检索 + GraphDB search-around。"""
    from biomed_ontology.ontology.neighborhood import GraphDbNeighborhood
    from biomed_ontology.pipeline import ensure_catalog_graphs
    from biomed_ontology.search import HybridSearcher

    if neighborhood is None:
        if ensure_graph:
            ensure_catalog_graphs(kb.graph, kb.concepts, kb.synonyms)
        neighborhood = GraphDbNeighborhood(kb.graph)
    return HybridSearcher(
        kb, backend=milvus_backend, neighborhood=neighborhood, chunk_store=chunk_store
    )


def attach_public_assist(searcher: Any, foundation: Any) -> Any:
    """把 WorldModel resolver / GraphDB 接到 HybridSearcher 公开臂。"""
    from biomed_ontology.search.public_assist import PublicLexicalExpand, PublicNenAssist

    world = getattr(foundation, "world", None)
    resolver = getattr(world, "resolver", None) if world is not None else None
    gdb = getattr(foundation, "graphdb", None)
    if resolver is not None and getattr(resolver, "index", None) is not None:
        searcher.nen_assist = PublicNenAssist(
            resolver.index,
            bern2=getattr(resolver, "bern2", None),
            graphdb=gdb,
        )
    searcher.lexical_expand = PublicLexicalExpand(
        bern2=getattr(resolver, "bern2", None) if resolver else None,
        graphdb=gdb,
    )
    return searcher


def open_dual_surface(
    *,
    bern2_url: str | None = None,
    literature_kb: Any | None = None,
    load_literature: bool = True,
    milvus_backend: Any | None = None,
    neighborhood: Any | None = None,
    searcher: Any | None = None,
    chunk_store: Any | None = None,
    allow_memory_chunk_store: bool = True,
) -> DualSurface:
    """装配双面 API（ToolApi + FoundationApi）；文献检索要求 Milvus。

    Parameters
    ----------
    literature_kb:
        测试夹具可注入已装好的文献 KB。
    milvus_backend / neighborhood / searcher:
        显式注入（``hmd eval`` / 单测）；未注入时要求本机 Milvus 集合已建好。
    chunk_store:
        显式注入；默认优先 Iceberg。单测夹具可传 ``MemoryChunkStore``。
    allow_memory_chunk_store:
        Iceberg 不可达时是否回落到内存（默认 True，便于本地/CI）；
        生产验收可传 False 强制湖表。
    """
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model
    from biomed_ontology.identity import IdentityService
    from biomed_ontology.tools import ToolApi

    world = load_world_model(bern2_url=bern2_url)
    identity = IdentityService.from_world(world)
    foundation = FoundationApi(world)
    foundation.identity = identity

    kb = literature_kb
    if kb is None and load_literature:
        from biomed_ontology.pipeline import build_literature_base

        kb = build_literature_base(with_graph=False)

    if kb is None:
        raise RuntimeError(
            "文献 ToolApi 未就绪：请提供 literature_kb，或先完成语料索引后使用 from_backends"
        )

    store = chunk_store or _default_chunk_store(kb, allow_memory=allow_memory_chunk_store)

    if searcher is None:
        backend = milvus_backend or _require_milvus_literature_backend(kb)
        searcher = build_literature_searcher(
            kb,
            milvus_backend=backend,
            neighborhood=neighborhood,
            chunk_store=store,
        )
    elif getattr(searcher, "chunk_store", None) is None:
        searcher.chunk_store = store
    attach_public_assist(searcher, foundation)

    tools = ToolApi.from_backends(
        kb=kb,
        backend=searcher.backend,
        foundation=foundation,
        searcher=searcher,
        chunk_store=store,
    )
    return DualSurface(
        tools=tools,
        foundation=foundation,
        kb=kb,
        search_backend="milvus",
        chunk_store=store,
        identity=identity,
    )


def _default_chunk_store(kb: Any, *, allow_memory: bool) -> Any:
    """生产默认 Iceberg；仅允许时回落 MemoryChunkStore。"""
    from biomed_ontology.lake.chunk_store import IcebergChunkStore, MemoryChunkStore

    release_id = str(getattr(kb, "release_id", "") or "")
    try:
        from biomed_ontology.lake.catalog import ensure_lake_tables

        ensure_lake_tables()
        return IcebergChunkStore(release_id=release_id)
    except Exception as exc:
        if not allow_memory:
            raise RuntimeError(
                f"Iceberg ChunkStore 不可用：{exc}；"
                "请先确保 lake catalog 可用并 hmd index dual-write，"
                "或 allow_memory_chunk_store=True"
            ) from exc
        import warnings

        warnings.warn(
            f"Iceberg ChunkStore 不可用，回落 MemoryChunkStore（非生产还原权威）：{exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return MemoryChunkStore(kb.chunks, documents=kb.documents, release_id=release_id)


def _require_milvus_literature_backend(kb: Any) -> Any:
    """集合已存在且可达时返回 MilvusBackend，否则硬失败。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.search.backends.milvus import MilvusBackend

    release_id = str(getattr(kb, "release_id", "") or "")
    try:
        backend = MilvusBackend(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            collection=settings.milvus_collection,
            embedder=get_embedder("multimodal-bio"),
            known_sources=frozenset(s.id for s in kb.registry.active()),
            release_id=release_id,
        )
        if not backend.client.has_collection(backend.collection):
            raise RuntimeError(
                f"Milvus 集合 {backend.collection!r} 不存在；请先 hmd index --recreate"
            )
        backend.require_release(release_id)
        return backend
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Milvus 文献后端不可用：{exc}；请 task milvus:up 并 hmd index") from exc
