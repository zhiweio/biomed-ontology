"""Enterprise Biomedical World Model — AI Data Foundation.

对上暴露 Semantic API；对下编排 Enterprise Ontology、BIOS、BERN2、
Entity Resolution、GraphDB Named Graphs、Evidence Index、OpenMetadata。

不做 Agent 编排。
"""

from __future__ import annotations

from biomed_ontology.foundation.api import SEMANTIC_OPS, FoundationApi
from biomed_ontology.foundation.world import WorldModel, load_world_model

__all__ = [
    "SEMANTIC_OPS",
    "FoundationApi",
    "WorldModel",
    "load_world_model",
]
