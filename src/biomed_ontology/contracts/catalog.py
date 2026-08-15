"""目录与证据的跨包 DTO / Protocol。Phase 2 IdentityService 在此收口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = ["ChunkView", "ClaimDraft", "ConceptCatalog"]


@runtime_checkable
class ConceptCatalog(Protocol):
    """术语目录只读面。``Normalizer`` 已满足。"""

    def concept(self, concept_id: str) -> object | None: ...


@dataclass(frozen=True)
class ChunkView:
    """证据切片的稳定视图（与 Iceberg / Milvus 的 chunk_id 对齐）。"""

    chunk_id: str
    doc_id: str
    text: str
    entity_ids: tuple[str, ...] = ()
    page: int | None = None


@dataclass(frozen=True)
class ClaimDraft:
    """抽取态 claim（``claim_status=extracted``）。validated 才进 graph/knowledge。"""

    claim_id: str
    subject_id: str
    predicate: str
    object_id: str | None = None
    object_value: str | None = None
    confidence: float = 1.0
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    span: str | None = None
    extracted_by: str = "unknown"
