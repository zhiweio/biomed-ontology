"""Foundation 运行时数据类（与 schema/hmd_enterprise.yaml 对齐）。

LinkML 契约：`task gen` → `_generated/hmd_enterprise.py`。
此处手写 dataclass 供 YAML seed / Resolver 热路径使用（生成物为 Pydantic）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AssetHit",
    "EnterpriseEntity",
    "EvidenceHit",
    "KnowledgeClaim",
    "ResolveHit",
]


@dataclass
class EnterpriseEntity:
    enterprise_id: str
    entity_kind: str
    preferred_label_en: str
    preferred_label_zh: str | None = None
    definition: str | None = None
    exact_match_xrefs: list[str] = field(default_factory=list)
    related_xrefs: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    status: str = "active"
    # kind-specific
    targets: list[str] = field(default_factory=list)
    indications: list[str] = field(default_factory=list)
    program_id: str | None = None
    modality: str | None = None
    therapeutic_area: str | None = None
    candidate_id: str | None = None
    target_ids: list[str] = field(default_factory=list)
    indication_ids: list[str] = field(default_factory=list)
    asset_fqn: str | None = None
    performed_on: str | None = None
    pmid: str | None = None
    doi: str | None = None
    mentions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], "")}


@dataclass
class KnowledgeClaim:
    claim_id: str
    subject_id: str
    predicate: str
    object_id: str | None = None
    object_value: str | None = None
    confidence: float = 1.0
    claim_status: str = "validated"
    source_count: int | None = None
    source_id: str | None = None
    source_type: str = "manual"
    extracted_by: str = "seed"
    evidence_ids: list[str] = field(default_factory=list)
    span: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], "")}


@dataclass
class EvidenceHit:
    evidence_id: str
    text: str
    entity_ids: list[str]
    doc_id: str | None = None
    chunk_id: str | None = None
    page: int | None = None
    quote: str | None = None
    collection: str = "literature"
    score: float = 0.0
    pmid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class AssetHit:
    asset_fqn: str
    name: str
    entity_ids: list[str]
    description: str | None = None
    asset_type: str = "dataset"
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ResolveHit:
    canonical_entity: str | None
    mention: str
    external_ids: list[str] = field(default_factory=list)
    bios_concepts: list[str] = field(default_factory=list)
    confidence: float = 0.0
    resolution_method: str = "unmapped"
    entity_kind: str | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)
