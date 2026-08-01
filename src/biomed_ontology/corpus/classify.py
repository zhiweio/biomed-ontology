"""文档标引分类（L4）。

规则通道先行、模型通道兜底。理由不是规则更准，而是规则可解释：
审校同事能直接看到"因为出现了 randomized controlled 所以打了 RCT 标签"，
从而判断该改规则还是该改标签定义。模型给出的概率无法支撑这个判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field

from biomed_ontology._generated.hmd_concept import (
    MappingJustificationEnum,
    ReviewStatusEnum,
)
from biomed_ontology._generated.hmd_taxonomy import TaxonomyDimensionEnum
from biomed_ontology.observability import Candidate, TraceContext

__all__ = [
    "DocumentLabel",
    "Taxonomy",
    "TaxonomyClassifier",
    "TaxonomyLabel",
    "load_taxonomy",
]


class TaxonomyLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_id: str
    dimension: TaxonomyDimensionEnum
    label_en: str
    label_zh: str | None = None
    definition: str | None = None
    parent_label: str | None = None
    keywords_en: list[str] = Field(default_factory=list)
    keywords_zh: list[str] = Field(default_factory=list)

    def keywords(self) -> list[str]:
        return [*self.keywords_en, *self.keywords_zh]


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_version: str
    labels: list[TaxonomyLabel]

    def by_dimension(self, dim: TaxonomyDimensionEnum) -> list[TaxonomyLabel]:
        return [ln for ln in self.labels if ln.dimension is dim]

    def get(self, label_id: str) -> TaxonomyLabel | None:
        return next((ln for ln in self.labels if ln.label_id == label_id), None)


def load_taxonomy(path: Path) -> Taxonomy:
    return Taxonomy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass
class DocumentLabel:
    doc_id: str
    label_id: str
    dimension: TaxonomyDimensionEnum
    confidence: float
    justification: MappingJustificationEnum
    matched_keywords: list[str]
    review_status: ReviewStatusEnum = ReviewStatusEnum.PENDING


class TaxonomyClassifier:
    """多标签标引。每个维度独立打标，维度之间互不排斥。"""

    # 单关键词命中就打标会误伤（"plus" 到处都是），两个以上才给高置信度。
    _CONF_BY_HITS: ClassVar[dict[int, float]] = {1: 0.55, 2: 0.8}
    _HIGH_CONF = 0.92

    def __init__(self, taxonomy: Taxonomy, *, min_confidence: float = 0.5) -> None:
        self.taxonomy = taxonomy
        self.min_confidence = min_confidence
        self._patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {
            ln.label_id: [(k, _kw_pattern(k)) for k in ln.keywords()] for ln in taxonomy.labels
        }

    def classify(
        self, doc_id: str, text: str, *, ctx: TraceContext | None = None
    ) -> list[DocumentLabel]:
        haystack = text.casefold()
        out: list[DocumentLabel] = []
        for label in self.taxonomy.labels:
            hits = [kw for kw, pat in self._patterns[label.label_id] if pat.search(haystack)]
            if not hits:
                continue
            conf = self._CONF_BY_HITS.get(len(hits), self._HIGH_CONF)
            if conf < self.min_confidence:
                continue
            out.append(
                DocumentLabel(
                    doc_id=doc_id,
                    label_id=label.label_id,
                    dimension=label.dimension,
                    confidence=conf,
                    justification=MappingJustificationEnum.LexicalMatching,
                    matched_keywords=hits,
                    # 规则标引一律 PENDING：可解释不等于正确，仍需抽样核验。
                    review_status=ReviewStatusEnum.PENDING,
                )
            )
        out.sort(key=lambda ln: (-ln.confidence, ln.label_id))
        if ctx is not None:
            ctx.record_decision(
                stage="CLASSIFY",
                justification=MappingJustificationEnum.LexicalMatching,
                chosen=",".join(ln.label_id for ln in out[:5]) or None,
                candidates=[
                    Candidate(ln.label_id, ln.confidence, "rule", label=ln.dimension.value)
                    for ln in out
                ],
                state_before=doc_id,
                state_after=f"labels={len(out)}",
                confidence=out[0].confidence if out else 0.0,
            )
        return out

    def coverage(self, labels_by_doc: dict[str, list[DocumentLabel]]) -> float:
        """标引覆盖率：至少命中一个标签的文档占比。完备性指标之一。"""
        if not labels_by_doc:
            return 0.0
        hit = sum(1 for v in labels_by_doc.values() if v)
        return hit / len(labels_by_doc)


def _kw_pattern(keyword: str) -> re.Pattern[str]:
    """英文关键词加词边界，中文不加 —— 中文没有词边界，加了永远匹配不上。"""
    kw = keyword.casefold()
    if re.search(r"[\u4e00-\u9fff]", kw):
        return re.compile(re.escape(kw))
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])")
