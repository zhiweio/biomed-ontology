"""版面解析后端。具体实现在 P10 落地，本层只暴露协议与注册表。"""

from __future__ import annotations

from biomed_ontology.parse.layout.base import (
    BlockKind,
    Capability,
    LayoutBackend,
    LayoutBlock,
    LayoutResult,
)
from biomed_ontology.parse.layout.registry import get_layout_backend

__all__ = [
    "BlockKind",
    "Capability",
    "LayoutBackend",
    "LayoutBlock",
    "LayoutResult",
    "get_layout_backend",
]
