"""双面运行时装配：ToolApi（文献）+ FoundationApi（World Model）。

运行时入口是 ``open_dual_surface()``。文献检索只认 Milvus + GraphDB 邻域；
身份 / 扩展经 Foundation ER + GraphDB。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["DualSurface", "build_literature_searcher", "open_dual_surface"]


@dataclass
class DualSurface:
    """KB 文献面 + World Model 面的统一句柄。"""

    tools: Any  # ToolApi
    foundation: Any  # FoundationApi
    kb: Any | None = None
    search_backend: str = "milvus"

    @property
    def world(self) -> Any:
        return self.foundation.world


def build_literature_searcher(
    kb: Any,
    *,
    milvus_backend: Any,
    neighborhood: Any | None = None,
    ensure_graph: bool = True,
) -> Any:
    """装配 HybridSearcher：Milvus 检索 + GraphDB search-around。"""
    from biomed_ontology.ontology.neighborhood import GraphDbNeighborhood
    from biomed_ontology.pipeline import ensure_catalog_graphs
    from biomed_ontology.search import HybridSearcher

    if neighborhood is None:
        if ensure_graph:
            ensure_catalog_graphs(kb.graph, kb.concepts, kb.synonyms)
        neighborhood = GraphDbNeighborhood(kb.graph)
    return HybridSearcher(kb, backend=milvus_backend, neighborhood=neighborhood)


def open_dual_surface(
    *,
    bern2_url: str | None = None,
    literature_kb: Any | None = None,
    load_literature: bool = True,
    milvus_backend: Any | None = None,
    neighborhood: Any | None = None,
    searcher: Any | None = None,
    prefer_milvus: bool = True,
) -> DualSurface:
    """装配双面 API。

    Parameters
    ----------
    literature_kb:
        测试夹具可注入已装好的文献 KB。
    milvus_backend / neighborhood / searcher:
        显式注入（``hmd eval`` / 单测）；未注入时要求本机 Milvus 集合已建好。
    prefer_milvus:
        保留兼容旧调用；产品路径始终要求 Milvus，不再回落内存词法。
    """
    del prefer_milvus  # 兼容形参；行为固定为 Milvus-only
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model
    from biomed_ontology.tools import ToolApi

    world = load_world_model(bern2_url=bern2_url)
    foundation = FoundationApi(world)

    kb = literature_kb
    if kb is None and load_literature:
        from biomed_ontology.pipeline import build_literature_base

        kb = build_literature_base(with_graph=False)

    if kb is None:
        raise RuntimeError(
            "文献 ToolApi 未就绪：请提供 literature_kb，或先完成语料索引后使用 from_backends"
        )

    if searcher is None:
        backend = milvus_backend or _require_milvus_literature_backend(kb)
        searcher = build_literature_searcher(
            kb, milvus_backend=backend, neighborhood=neighborhood
        )
    tools = ToolApi.from_backends(
        kb=kb, backend=searcher.backend, foundation=foundation, searcher=searcher
    )
    return DualSurface(
        tools=tools, foundation=foundation, kb=kb, search_backend="milvus"
    )


def _require_milvus_literature_backend(kb: Any) -> Any:
    """集合已存在且可达时返回 MilvusBackend，否则硬失败（禁内存词法回落）。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.search.backends.milvus import MilvusBackend

    try:
        backend = MilvusBackend(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            collection=settings.milvus_collection,
            embedder=get_embedder("multimodal-bio"),
            known_sources=frozenset(s.id for s in kb.registry.active()),
        )
        if not backend.client.has_collection(backend.collection):
            raise RuntimeError(
                f"Milvus 集合 {backend.collection!r} 不存在；请先 hmd index --recreate"
            )
        return backend
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Milvus 文献后端不可用：{exc}；请 task milvus:up 并 hmd index"
        ) from exc
