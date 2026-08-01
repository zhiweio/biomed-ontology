"""检索后端注册表。Milvus 实现在 P12 接入，届时在 `get_backend` 里加分支。"""

from __future__ import annotations

from biomed_ontology.search.backends.base import (
    BackendResult,
    ChunkMeta,
    LicenseScope,
    RetrievalRequest,
    SearchBackend,
)
from biomed_ontology.search.backends.local import Bm25Index, DenseIndex, LocalBackend

__all__ = [
    "BackendResult",
    "Bm25Index",
    "ChunkMeta",
    "DenseIndex",
    "LicenseScope",
    "LocalBackend",
    "RetrievalRequest",
    "SearchBackend",
]
