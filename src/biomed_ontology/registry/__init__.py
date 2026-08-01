"""数据源注册表 —— L0 层。

注册表回答三个问题：数据从哪来、什么许可、当前是否启用。
所有 loader 都必须先在此注册，未注册的源不允许写入术语层 ——
否则许可元数据会缺失，导出闸门无从判断。
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from biomed_ontology._generated.hmd_concept import EntityTypeEnum, LicenseTierEnum
from biomed_ontology.licensing import named_graph_uri, tier_rank

REGISTRY_PATH = Path(__file__).parent / "sources.yaml"

__all__ = [
    "DownloadSpec",
    "SourceDefinition",
    "SourceRegistry",
    "SourceRole",
    "Track",
    "load_registry",
]


class Track(str, Enum):
    """双轨策略：A 轨开放许可跑通方法论，B 轨采购数据做覆盖度跃升。"""

    A = "A"
    B = "B"


class SourceRole(str, Enum):
    """源在建团与归一化中的角色，决定冲突时谁说了算。"""

    AUTHORITATIVE = "authoritative"
    """权威源。等价团选 primary_xref 时优先，冲突时以它为准。"""

    SUPPORTING = "supporting"
    """补充源。提供额外别名与层级，不参与仲裁。"""

    VALIDATION = "validation"
    """校验源。只用于交叉核对，不直接写入术语层。"""

    CHINESE_LAYER = "chinese_layer"
    """中文层来源。"""

    FACT_SEED = "fact_seed"
    """结构化事实种子，供应商数据归一化后进事实层。"""

    CORPUS = "corpus"
    """文档语料，不产出概念。"""


class DownloadSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    format: str
    notes: str | None = None


class SourceDefinition(BaseModel):
    """一个数据源的完整声明。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    track: Track
    license_id: str
    license_tier: LicenseTierEnum
    role: SourceRole
    enabled: bool = True
    homepage: str | None = None
    bioregistry_prefix: str | None = None
    entity_types: list[EntityTypeEnum] = Field(default_factory=list)
    authoritative_for: list[EntityTypeEnum] | None = None
    """权威范围。一个源可以只对部分实体类型权威 ——
    NCIt 的 MoA 分类是主干，但疾病层级要让位给 MONDO。
    留空则由 role 推导。"""

    requires_credentials: bool = False
    procurement_priority: int | None = None
    download: DownloadSpec | None = None
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _id_is_upper_snake(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or v != v.upper():
            raise ValueError(f"source id 必须是大写 SNAKE_CASE: {v!r}")
        return v

    @property
    def named_graph(self) -> str:
        return named_graph_uri(self.id, self.license_tier)

    @property
    def authority_scope(self) -> frozenset[EntityTypeEnum]:
        if self.authoritative_for is not None:
            return frozenset(self.authoritative_for)
        if self.role is SourceRole.AUTHORITATIVE:
            return frozenset(self.entity_types)
        return frozenset()

    @property
    def is_authority(self) -> bool:
        return bool(self.authority_scope)

    @property
    def is_procurement_slot(self) -> bool:
        """Track B 且未启用 = 已建好插槽、等采购到位。"""
        return self.track is Track.B and not self.enabled


class SourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_version: str
    sources: list[SourceDefinition]

    @field_validator("sources")
    @classmethod
    def _ids_unique(cls, v: list[SourceDefinition]) -> list[SourceDefinition]:
        ids = [s.id for s in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"重复的 source id: {sorted(dupes)}")
        return v

    def __getitem__(self, source_id: str) -> SourceDefinition:
        try:
            return next(s for s in self.sources if s.id == source_id)
        except StopIteration:
            raise KeyError(f"未注册的数据源: {source_id!r}") from None

    def __contains__(self, source_id: object) -> bool:
        return any(s.id == source_id for s in self.sources)

    def __iter__(self) -> Any:
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    def active(self) -> list[SourceDefinition]:
        return [s for s in self.sources if s.enabled]

    def by_track(self, track: Track) -> list[SourceDefinition]:
        return [s for s in self.sources if s.track is track]

    def by_entity_type(self, entity_type: EntityTypeEnum) -> list[SourceDefinition]:
        return [s for s in self.active() if entity_type in s.entity_types]

    def authoritative_for(self, entity_type: EntityTypeEnum) -> list[SourceDefinition]:
        return [s for s in self.by_entity_type(entity_type) if entity_type in s.authority_scope]

    def visible_to(self, entitlements: frozenset[str]) -> list[SourceDefinition]:
        """查询重写用：调用方能看到哪些源。

        TIER_2/3 需要持有该源的凭据；TIER_0/1 对内部一律可见。
        """
        unrestricted = tier_rank(LicenseTierEnum.TIER_1)
        return [
            s
            for s in self.active()
            if tier_rank(s.license_tier) <= unrestricted or s.id in entitlements
        ]

    def procurement_slots(self) -> list[SourceDefinition]:
        """按采购优先级排序的待采购源，供采购 ROI 论证使用。"""
        slots = [s for s in self.sources if s.is_procurement_slot]
        return sorted(slots, key=lambda s: (s.procurement_priority or 999, s.id))


@lru_cache(maxsize=1)
def load_registry(path: Path | None = None) -> SourceRegistry:
    raw = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    return SourceRegistry.model_validate(raw)
