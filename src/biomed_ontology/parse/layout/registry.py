"""版面后端注册表：配置开关的唯一落点。

后端在此按名取用，而不是各调用处自己 import —— 于是"启用某个后端"这件事
有且只有一处入口，法务闸门也就只需要装在这一处。
"""

from __future__ import annotations

from biomed_ontology.config import Settings
from biomed_ontology.config import settings as default_settings
from biomed_ontology.licensing import assert_component_cleared
from biomed_ontology.parse.layout.base import LayoutBackend, LayoutBlock, LayoutResult

__all__ = ["LayoutBackend", "LayoutBlock", "LayoutResult", "get_layout_backend"]


def get_layout_backend(name: str | None = None, *, config: Settings | None = None) -> LayoutBackend:
    cfg = config or default_settings
    backend = name or cfg.layout_backend
    assert_component_cleared(backend, accept_uncleared=cfg.accept_uncleared_components)

    if backend == "pymupdf":
        from biomed_ontology.parse.layout.pymupdf import PyMuPDFBackend

        return PyMuPDFBackend(max_pages=cfg.parse_max_pages, max_bytes=cfg.parse_max_bytes)
    if backend == "mineru":
        from biomed_ontology.parse.layout.mineru import MinerUBackend

        return MinerUBackend(
            base_url=cfg.mineru_base_url,
            api_key=cfg.mineru_api_key.get_secret_value(),
            timeout_s=cfg.mineru_timeout_s,
        )
    raise ValueError(f"未知版面后端：{backend!r}")
