"""种子切片加载与概念构建。

种子文件承载的是买不到的部分：概念范围与企业内部别名。
外部 ID 交给各源 loader 从真实快照解析 —— 手抄的 ID 无法与快照版本对齐。

构建过程会做一件种子文件本身做不到的事：跨概念检测 alias_norm 碰撞。
人工登记的歧义表总有遗漏，碰撞检测能把漏网的歧义在入库前抓出来。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from biomed_ontology._generated.hmd_concept import (
    AliasTypeEnum,
    EntityTypeEnum,
    LanguageEnum,
    LicenseTierEnum,
    ReviewStatusEnum,
    SynonymScopeEnum,
)
from biomed_ontology.alias import generate_code_variants, normalize_alias
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger
from biomed_ontology.registry import SourceRegistry

__all__ = [
    "LINK_PREDICATES",
    "AmbiguityRegistry",
    "BuiltConcept",
    "BuiltSynonym",
    "ConceptLink",
    "SeedAlias",
    "SeedBuildResult",
    "SeedConcept",
    "SeedFile",
    "build_from_seed",
    "load_ambiguity_registry",
    "load_seed_file",
]

# 种子内部引用的伪源，不在 registry 中；构建时映射为 SEED_INTERNAL 的许可 tier。
_SEED_SOURCE = "SEED_INTERNAL"

# 种子字段名 → (正向谓词, 反向谓词)。
#
# 反向谓词必须显式登记，因为检索期两个方向都要走：问「MET 抑制剂有哪些」是从
# 靶点找药（反向），问「赛沃替尼打什么靶点」是从药找靶点（正向）。
# gold 里 Q4「VEGFR2 抑制剂 抗血管生成」问的就是反向那一路。
#
# 谓词名与 `hmd_fact` 的抽取谓词对齐（has_target / treats），
# 这样种子断言的边和从正文抽出来的边在 SPARQL 里是同一个谓词、
# 靠命名图区分来源，而不是靠两套词汇表。
LINK_PREDICATES: dict[str, tuple[str, str]] = {
    "targets": ("has_target", "targeted_by"),
    "indications": ("treats", "treated_by"),
}


class SeedAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str
    lang: LanguageEnum
    scope: SynonymScopeEnum
    type: AliasTypeEnum
    source: str


class XrefHint(BaseModel):
    model_config = ConfigDict(extra="allow")

    by: str
    value: str


class SeedConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    preferred_label_en: str
    preferred_label_zh: str | None = None
    definition: str | None = None
    verified: bool = False
    xref_hints: dict[str, XrefHint] = Field(default_factory=dict)
    aliases: list[SeedAlias] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    indications: list[str] = Field(default_factory=list)
    modality: str | None = None


class SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_version: str
    entity_type: EntityTypeEnum
    concepts: list[SeedConcept]


class AmbiguitySense(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_key: str
    entity_type: EntityTypeEnum
    prior: float
    context_cues: list[str] = Field(default_factory=list)


class AmbiguousAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    senses: list[AmbiguitySense]
    note: str | None = None
    resolved: bool = False
    """已确认无歧义。登记在册避免重复排查。"""

    @property
    def is_ambiguous(self) -> bool:
        return not self.resolved and len(self.senses) > 1


class AmbiguityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_version: str
    ambiguous_aliases: list[AmbiguousAlias]

    def norm_index(self) -> dict[str, AmbiguousAlias]:
        return {normalize_alias(a.alias): a for a in self.ambiguous_aliases}


# ---------------------------------------------------------------- 构建产物


@dataclass
class BuiltSynonym:
    alias_id: str
    concept_id: str
    alias_raw: str
    alias_norm: str
    lang: LanguageEnum
    scope: SynonymScopeEnum
    alias_type: AliasTypeEnum
    source: str
    license_tier: LicenseTierEnum
    review_status: ReviewStatusEnum
    is_ambiguous: bool = False
    confidence: float = 1.0
    is_generated_variant: bool = False
    """规则生成的写法变体，不是源里的原始别名。审校时优先级低于原始别名。"""


@dataclass(frozen=True)
class ConceptLink:
    """一条类型化链接。`object_id` 已解析为 concept_id，不是种子 key。

    做成通用的 (谓词, 对象) 而不是 `targets` / `indications` 两个业务字段：
    下一个关系（`combination_with`、`biomarker_for`……）加进来时应该只改
    `LINK_PREDICATES` 一张表，而不是再给 dataclass、RDF 发射、图通道各加一处分支。
    """

    predicate: str
    object_id: str


@dataclass
class BuiltConcept:
    concept_id: str
    seed_key: str
    entity_type: EntityTypeEnum
    preferred_label_en: str
    preferred_label_zh: str | None
    definition: str | None
    parents: list[str] = field(default_factory=list)
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    review_status: ReviewStatusEnum = ReviewStatusEnum.PENDING
    # 类型化链接（药→靶点、药→适应症）。种子里一直写着这些关系，
    # 但此前只有 parents 会被 copy 过来，它们在 ingest 阶段被静默丢掉，
    # 于是「本体」在检索期实际只剩一条 skos:broader 可走。
    links: list[ConceptLink] = field(default_factory=list)


@dataclass
class SeedBuildResult:
    concepts: list[BuiltConcept]
    synonyms: list[BuiltSynonym]
    ambiguity_collisions: dict[str, list[str]] = field(default_factory=dict)
    """alias_norm → 命中的多个 concept_id。空表示无未登记歧义。"""

    unregistered_collisions: dict[str, list[str]] = field(default_factory=dict)
    """碰撞检测发现、但歧义表未登记的条目。这是必须人工处理的队列。"""

    unresolved_parents: dict[str, list[str]] = field(default_factory=dict)
    """concept_id → 无法解析的父节点 key。非空即为种子文件写错了上位概念。"""

    unresolved_links: dict[str, list[str]] = field(default_factory=dict)
    """concept_id → 无法解析的链接端点，形如 `has_target:MET`。

    与 unresolved_parents 同样处置：报出来，不静默丢。链接指向一个不存在的概念时，
    检索期的 search-around 会安静地少走一条路 —— 表现为"某类 query 就是召不回来"，
    而查的人会一路查到融合权重上去。"""

    def concept_by_key(self, key: str) -> BuiltConcept | None:
        return next((c for c in self.concepts if c.seed_key == key), None)


# ---------------------------------------------------------------- 加载


def load_seed_file(path: Path) -> SeedFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SeedFile.model_validate(raw)


def load_ambiguity_registry(path: Path) -> AmbiguityRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AmbiguityRegistry.model_validate(raw)


# ---------------------------------------------------------------- 构建


def build_from_seed(
    seed_files: list[Path],
    *,
    registry: SourceRegistry,
    id_ledger: IdLedger,
    alias_ledger: SequenceLedger,
    ambiguity: AmbiguityRegistry | None = None,
    generate_variants: bool = True,
) -> SeedBuildResult:
    """从种子文件构建概念与别名。

    ID 通过 ledger 分配，因此重复调用产出相同 ID —— 这是离线可重放构建的前提。
    """
    ambiguity_index = ambiguity.norm_index() if ambiguity else {}
    concepts: list[BuiltConcept] = []
    synonyms: list[BuiltSynonym] = []
    norm_to_concepts: dict[str, set[str]] = defaultdict(set)
    seen_rows: set[str] = set()

    for path in seed_files:
        seed = load_seed_file(path)
        for sc in seed.concepts:
            tier = _concept_tier(sc, registry)
            # 种子 key 参与建团，保证概念在外部 ID 解析出来之前就有稳定锚点。
            anchor = f"seedkey:{seed.entity_type.value.lower()}:{sc.key}"
            mint = id_ledger.mint(seed.entity_type, {anchor})
            concept_id = mint.concept_id

            concepts.append(
                BuiltConcept(
                    concept_id=concept_id,
                    seed_key=sc.key,
                    entity_type=seed.entity_type,
                    preferred_label_en=sc.preferred_label_en,
                    preferred_label_zh=sc.preferred_label_zh,
                    definition=sc.definition,
                    parents=list(sc.parents),
                    links=_seed_links(sc),
                    license_tier=tier,
                    review_status=(
                        ReviewStatusEnum.APPROVED if sc.verified else ReviewStatusEnum.PENDING
                    ),
                )
            )

            for sa in sc.aliases:
                built = _build_synonym(sa, concept_id, registry, alias_ledger, is_variant=False)
                if _record(built, synonyms, seen_rows):
                    norm_to_concepts[built.alias_norm].add(concept_id)

                if generate_variants:
                    for variant in sorted(generate_code_variants(sa.raw)):
                        v = _build_synonym(
                            sa.model_copy(update={"raw": variant}),
                            concept_id,
                            registry,
                            alias_ledger,
                            is_variant=True,
                        )
                        if _record(v, synonyms, seen_rows):
                            norm_to_concepts[v.alias_norm].add(concept_id)

    collisions = {n: sorted(c) for n, c in norm_to_concepts.items() if len(c) > 1}
    unregistered = {n: c for n, c in collisions.items() if n not in ambiguity_index}

    _mark_ambiguous(synonyms, collisions, ambiguity_index)
    unresolved = _resolve_parents(concepts)
    unresolved_links = _resolve_links(concepts)

    return SeedBuildResult(
        concepts=concepts,
        synonyms=synonyms,
        ambiguity_collisions=collisions,
        unregistered_collisions=unregistered,
        unresolved_parents=unresolved,
        unresolved_links=unresolved_links,
    )


def _seed_links(sc: SeedConcept) -> list[ConceptLink]:
    """把种子里的 `targets` / `indications` 收成统一形状。此处仍是种子 key，
    解析成 concept_id 要等全部文件读完 —— 药引用的靶点在另一个文件里。"""
    out: list[ConceptLink] = []
    for field_name, (predicate, _inverse) in LINK_PREDICATES.items():
        for key in getattr(sc, field_name, ()) or ():
            out.append(ConceptLink(predicate=predicate, object_id=key))
    return out


def _resolve_parents(concepts: list[BuiltConcept]) -> dict[str, list[str]]:
    """种子里的 parents 写的是人可读的 key，落库前必须换成 concept_id。

    不换的话，图里的 skos:broader 会指向不存在的 IRI：SPARQL 层级查询静默返回空，
    而这类"查得到但查不全"的故障比直接报错难发现得多。
    """
    by_key = {c.seed_key: c.concept_id for c in concepts}
    by_id = {c.concept_id for c in concepts}
    unresolved: dict[str, list[str]] = {}
    for c in concepts:
        resolved, missing = [], []
        for p in c.parents:
            if p in by_id:
                resolved.append(p)
            elif p in by_key:
                resolved.append(by_key[p])
            else:
                missing.append(p)
        c.parents = resolved
        if missing:
            unresolved[c.concept_id] = missing
    return unresolved


def _resolve_links(concepts: list[BuiltConcept]) -> dict[str, list[str]]:
    """与 `_resolve_parents` 同一套 key→concept_id 解析，作用在类型化链接上。

    种子里的靶点写成 `MET`、适应症写成 `nsclc`，而它们各自的 concept_id 由
    ledger 分配、跨文件才知道。解析不了的不静默丢：写进 `unresolved_links`
    交给上层告警，否则 `targets: [KDR]` 里的一个拼写错误会表现为
    "VEGFR2 那类 query 就是查不到药"，而现场看不出是本体写错了。
    """
    by_key = {c.seed_key: c.concept_id for c in concepts}
    by_id = {c.concept_id for c in concepts}
    unresolved: dict[str, list[str]] = {}
    for c in concepts:
        resolved, missing = [], []
        for link in c.links:
            if link.object_id in by_id:
                resolved.append(link)
            elif link.object_id in by_key:
                resolved.append(ConceptLink(link.predicate, by_key[link.object_id]))
            else:
                missing.append(f"{link.predicate}:{link.object_id}")
        c.links = resolved
        if missing:
            unresolved[c.concept_id] = missing
    return unresolved


def _row_key(concept_id: str, alias_raw: str, lang: LanguageEnum) -> str:
    return f"{concept_id}|{alias_raw}|{lang.value}"


def _record(syn: BuiltSynonym, synonyms: list[BuiltSynonym], seen: set[str]) -> bool:
    key = _row_key(syn.concept_id, syn.alias_raw, syn.lang)
    if key in seen:
        return False
    seen.add(key)
    synonyms.append(syn)
    return True


def _build_synonym(
    sa: SeedAlias,
    concept_id: str,
    registry: SourceRegistry,
    alias_ledger: SequenceLedger,
    *,
    is_variant: bool,
) -> BuiltSynonym:
    # alias_id 标识别名表里的一行，因此按 alias_raw 而非 alias_norm 分配 ——
    # HMPL-504 与 HMPL504 归一化后同键，但在 BM25 索引里是两个不同的可匹配串。
    alias_id = alias_ledger.assign(_row_key(concept_id, sa.raw, sa.lang))
    return BuiltSynonym(
        alias_id=alias_id,
        concept_id=concept_id,
        alias_raw=sa.raw,
        alias_norm=normalize_alias(sa.raw),
        lang=sa.lang,
        scope=sa.scope,
        alias_type=sa.type,
        source=sa.source,
        license_tier=_source_tier(sa.source, registry),
        # 规则生成的变体没有源头背书，一律待审校。
        review_status=(
            ReviewStatusEnum.PENDING if is_variant else _source_review_status(sa.source, registry)
        ),
        confidence=0.9 if is_variant else 1.0,
        is_generated_variant=is_variant,
    )


def _mark_ambiguous(
    synonyms: list[BuiltSynonym],
    collisions: dict[str, list[str]],
    ambiguity_index: dict[str, AmbiguousAlias],
) -> None:
    for s in synonyms:
        registered = ambiguity_index.get(s.alias_norm)
        if registered is not None:
            s.is_ambiguous = registered.is_ambiguous
        elif s.alias_norm in collisions:
            s.is_ambiguous = True


def _source_tier(source: str, registry: SourceRegistry) -> LicenseTierEnum:
    if source == _SEED_SOURCE or source not in registry:
        return LicenseTierEnum.TIER_0
    return registry[source].license_tier


def _source_review_status(source: str, registry: SourceRegistry) -> ReviewStatusEnum:
    """权威源免审校，其余待审。"""
    if source == _SEED_SOURCE or source not in registry:
        return ReviewStatusEnum.PENDING
    from biomed_ontology.registry import SourceRole

    if registry[source].role is SourceRole.AUTHORITATIVE:
        return ReviewStatusEnum.AUTO_TRUSTED
    return ReviewStatusEnum.PENDING


def _concept_tier(sc: SeedConcept, registry: SourceRegistry) -> LicenseTierEnum:
    """概念的 tier 取其所有别名来源中的最高值 —— 可见性由最严的那个源决定。"""
    tiers = [_source_tier(a.source, registry) for a in sc.aliases]
    if not tiers:
        return LicenseTierEnum.TIER_0
    order = [
        LicenseTierEnum.TIER_0,
        LicenseTierEnum.TIER_1,
        LicenseTierEnum.TIER_2,
        LicenseTierEnum.TIER_3,
    ]
    return max(tiers, key=order.index)
