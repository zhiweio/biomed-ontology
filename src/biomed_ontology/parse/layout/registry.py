"""版面后端注册表：配置开关的唯一落点。

后端在此按名取用，而不是各调用处自己 import —— 于是"启用某个后端"这件事
有且只有一处入口，法务闸门也就只需要装在这一处。

`auto` 不是具体后端：由 Document Router 决策后再按名取用。
`pymupdf` 已废弃，传入即报错。
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
    if backend == "auto":
        raise ValueError("layout_backend=auto 须经 Document Router 解析为具体后端后再取用")
    if backend == "pymupdf":
        raise ValueError("版面后端 'pymupdf' 已废弃，请改用 'pymupdf4llm'")

    assert_component_cleared(backend, accept_uncleared=cfg.accept_uncleared_components)

    if backend == "pymupdf4llm":
        from biomed_ontology.parse.layout.pymupdf4llm import PyMuPDF4LLMBackend

        return PyMuPDF4LLMBackend(max_pages=cfg.parse_max_pages, max_bytes=cfg.parse_max_bytes)
    if backend == "docling":
        from biomed_ontology.parse.layout.docling import DoclingBackend

        return DoclingBackend(
            max_pages=cfg.parse_max_pages,
            max_bytes=cfg.parse_max_bytes,
            render_chart_images=cfg.docling_render_chart_images,
        )
    if backend == "mineru":
        from biomed_ontology.parse.layout.mineru import MinerUBackend

        return MinerUBackend(
            transport=cfg.mineru_transport,
            base_url=cfg.mineru_base_url,
            api_key=cfg.mineru_api_key.get_secret_value(),
            timeout_s=cfg.mineru_timeout_s,
            mineru_backend=cfg.mineru_engine,
            parse_method=cfg.mineru_parse_method,
            lang=cfg.mineru_lang,
            formula_enable=cfg.mineru_formula_enable,
            table_enable=cfg.mineru_table_enable,
            effort=cfg.mineru_effort,
            max_pages=cfg.parse_max_pages,
            max_bytes=cfg.parse_max_bytes,
        )
    raise ValueError(f"未知版面后端：{backend!r}")
