"""双面运行时装配：ToolApi（文献）+ FoundationApi（World Model）。

运行时入口是 ``open_dual_surface()``，不把 ``build_knowledge_base()`` 当作对外契约。
文献装配走 ``build_literature_base()``（``HMD:ENT:*``）；检索优先 Milvus literature，
本地 LocalBackend 仅作无索引时的回落。身份 / 扩展经 Foundation ER + GraphDB。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["DualSurface", "open_dual_surface"]


@dataclass
class DualSurface:
    """KB 文献面 + World Model 面的统一句柄。"""

    tools: Any  # ToolApi
    foundation: Any  # FoundationApi
    kb: Any | None = None
    search_backend: str = "local"

    @property
    def world(self) -> Any:
        return self.foundation.world


def open_dual_surface(
    *,
    bern2_url: str | None = None,
    literature_kb: Any | None = None,
    load_literature: bool = True,
    milvus_backend: Any | None = None,
    prefer_milvus: bool = False,
) -> DualSurface:
    """装配双面 API。

    Parameters
    ----------
    literature_kb:
        测试夹具可注入已装好的文献 KB。
    milvus_backend:
        显式注入的 ``MilvusBackend``（``hmd eval`` / index 后）。
    prefer_milvus:
        为 True 时若 ``hmd_chunks`` 已存在则挂载真 embedder 的 MilvusBackend；
        默认 False，避免 demo/单测拉起大模型。生产 ``hmd serve`` 可打开。
    """
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model
    from biomed_ontology.tools import ToolApi

    world = load_world_model(bern2_url=bern2_url)
    foundation = FoundationApi(world)

    kb = literature_kb
    if kb is None and load_literature:
        from biomed_ontology.pipeline import build_literature_base

        # 运行时默认不灌 KB 命名图；完整图面需 with_graph=True / gate
        kb = build_literature_base(with_graph=False)

    if kb is None:
        raise RuntimeError(
            "文献 ToolApi 未就绪：请提供 literature_kb，或先完成语料索引后使用 from_backends"
        )

    backend = milvus_backend
    backend_name = "local"
    if backend is None and prefer_milvus:
        backend = _try_milvus_literature_backend(kb)
    if backend is not None:
        backend_name = getattr(backend, "name", "milvus")

    tools = ToolApi.from_backends(kb=kb, backend=backend, foundation=foundation)
    return DualSurface(
        tools=tools, foundation=foundation, kb=kb, search_backend=backend_name
    )


def _try_milvus_literature_backend(kb: Any) -> Any | None:
    """集合已存在且可达时返回 MilvusBackend，否则 None（回落 Local）。"""
    try:
        from biomed_ontology.config import settings
        from biomed_ontology.embed import get_embedder
        from biomed_ontology.search.backends.milvus import MilvusBackend

        backend = MilvusBackend(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            collection=settings.milvus_collection,
            embedder=get_embedder("multimodal-bio"),
            known_sources=frozenset(s.id for s in kb.registry.active()),
        )
        if not backend.client.has_collection(backend.collection):
            return None
        return backend
    except Exception:
        return None
