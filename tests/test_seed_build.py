"""种子切片构建。

其中 test_savolitinib_* 是 D4 旗舰用例的自动化版本 ——
「多种别名映射到同一唯一 code」这个核心诉求，成立与否由这几条断言决定。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology._generated.hmd_concept import (
    AliasTypeEnum,
    EntityTypeEnum,
    LanguageEnum,
    ReviewStatusEnum,
    SynonymScopeEnum,
)
from biomed_ontology.alias import normalize_alias
from biomed_ontology.ingest.seed import build_from_seed
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger

# D4：这些写法必须全部归到同一个 HMD:SUB code。
SAVOLITINIB_SPELLINGS = [
    "savolitinib",
    "volitinib",
    "HMPL-504",
    "HMPL504",
    "HMPL 504",
    "AZD6094",
    "AZD-6094",
    "ORPATHYS",
    "沃瑞沙",
    "赛沃替尼",
    "沃利替尼",
]


def _lookup(build, alias: str) -> set[str]:
    norm = normalize_alias(alias)
    return {s.concept_id for s in build.synonyms if s.alias_norm == norm}


def test_savolitinib_all_spellings_resolve_to_one_code(build):
    resolved = {alias: _lookup(build, alias) for alias in SAVOLITINIB_SPELLINGS}

    missing = [a for a, cids in resolved.items() if not cids]
    assert not missing, f"未收录的写法: {missing}"

    codes = {cid for cids in resolved.values() for cid in cids}
    assert len(codes) == 1, f"同一药物落到了多个 code: {codes}"


def test_savolitinib_code_is_in_substance_namespace(build):
    (code,) = _lookup(build, "HMPL-504")
    assert code.startswith("HMD:SUB:")


def test_savolitinib_chinese_and_english_share_code(build):
    """跨语言互通：中文商品名与英文 INN 必须同码。"""
    assert _lookup(build, "沃瑞沙") == _lookup(build, "savolitinib")


def test_partner_code_alias_type_is_preserved(build):
    """AZD6094 是合作方代号，与内部代号来源不同，审校优先级也不同。"""
    azd = next(s for s in build.synonyms if s.alias_raw == "AZD6094")
    hmpl = next(s for s in build.synonyms if s.alias_raw == "HMPL-504")
    assert azd.alias_type is AliasTypeEnum.PARTNER_CODE
    assert hmpl.alias_type is AliasTypeEnum.INTERNAL_CODE


# ---------------------------------------------------------------- scope 语义


def test_broad_aliases_are_not_treated_as_equivalent(build):
    """PI3K 是家族名。标 EXACT 会让查 PI3Kδ 抑制剂召回全部 PI3K 文献。"""
    pi3k = [s for s in build.synonyms if s.alias_norm == "pi3k"]
    assert pi3k
    assert all(s.scope is SynonymScopeEnum.BROAD for s in pi3k)


def test_component_of_combination_drug_is_narrow(build):
    """trifluridine 是复方的组分而非复方本身。"""
    tri = next(s for s in build.synonyms if s.alias_raw == "trifluridine")
    assert tri.scope is SynonymScopeEnum.NARROW


def test_every_alias_has_a_scope(build):
    assert all(isinstance(s.scope, SynonymScopeEnum) for s in build.synonyms)


# ---------------------------------------------------------------- 归一化联动


def test_greek_and_ascii_spellings_share_code(build):
    """PI3Kδ 与 PI3K-delta 必须同码，否则查询侧会静默丢召回。"""
    assert _lookup(build, "PI3Kδ") == _lookup(build, "PI3K-delta") != set()


def test_code_variants_are_generated_and_marked(build):
    """HMPL504 未写在种子里，由规则生成 —— 必须标记来源并置为待审校。"""
    variant = next(s for s in build.synonyms if s.alias_raw == "HMPL504")
    assert variant.is_generated_variant
    assert variant.review_status is ReviewStatusEnum.PENDING
    assert variant.confidence < 1.0


# ---------------------------------------------------------------- 歧义


def test_registered_ambiguous_aliases_are_flagged(build):
    """MET / ITP / NET 命中歧义表，禁止直接单选。"""
    for alias in ["MET", "ITP", "NET"]:
        hits = [s for s in build.synonyms if s.alias_norm == normalize_alias(alias)]
        assert hits, f"{alias} 未收录"
        assert all(s.is_ambiguous for s in hits), f"{alias} 应标记为歧义"


def test_unambiguous_alias_is_not_flagged(build):
    savo = [s for s in build.synonyms if s.alias_norm == "savolitinib"]
    assert not any(s.is_ambiguous for s in savo)


def test_resolved_entry_is_not_flagged_as_ambiguous(ambiguity):
    """ORR 已确认单义，登记在册只为避免重复排查。"""
    orr = next(a for a in ambiguity.ambiguous_aliases if a.alias == "ORR")
    assert orr.resolved
    assert not orr.is_ambiguous


def test_no_unregistered_collisions_in_seed(build):
    """碰撞检测兜住歧义表的遗漏。非空即为必须人工处理的队列。"""
    assert build.unregistered_collisions == {}, f"发现未登记歧义: {build.unregistered_collisions}"


def test_ambiguity_priors_are_a_distribution(ambiguity):
    for entry in ambiguity.ambiguous_aliases:
        total = sum(s.prior for s in entry.senses)
        assert total == pytest.approx(1.0, abs=0.01), f"{entry.alias} 先验和为 {total}"


def test_ambiguous_senses_have_context_cues(ambiguity):
    """cue 是消歧最便宜的一道证据，缺失会把成本推给 LLM。"""
    for entry in ambiguity.ambiguous_aliases:
        if not entry.is_ambiguous:
            continue
        for sense in entry.senses:
            assert sense.context_cues, f"{entry.alias}/{sense.concept_key} 缺少 context_cues"


# ---------------------------------------------------------------- 结构不变量


def test_alias_ids_are_unique(build):
    ids = [s.alias_id for s in build.synonyms]
    assert len(ids) == len(set(ids))


def test_concept_ids_are_unique(build):
    ids = [c.concept_id for c in build.concepts]
    assert len(ids) == len(set(ids))


def test_every_alias_points_at_an_existing_concept(build):
    known = {c.concept_id for c in build.concepts}
    assert all(s.concept_id in known for s in build.synonyms)


def test_parent_references_resolve(build):
    """parents 在构建末尾会由 seed_key 改写成 concept_id。

    这一步必须在 build 内完成而不是留给下游：任何一个下游忘了改写，
    层级就会静默断掉，而断掉的层级在检索结果里表现为"少召回几条"，
    没人会把它归因到本体上。
    """
    known = {c.concept_id for c in build.concepts}
    assert not build.unresolved_parents, f"存在无法解析的父概念：{build.unresolved_parents}"
    for c in build.concepts:
        for parent in c.parents:
            assert parent in known, f"{c.seed_key} 的父概念 {parent} 未解析为 concept_id"


def test_unverified_concepts_stay_pending(build):
    """verified: false 的概念不得进入发版。"""
    assert all(c.review_status is ReviewStatusEnum.PENDING for c in build.concepts)


def test_chinese_labels_present_for_all_concepts(build):
    """中英双语互通是硬需求，缺中文标签的概念在中文查询下不可达。"""
    missing = [c.seed_key for c in build.concepts if not c.preferred_label_zh]
    assert not missing, f"缺少中文标签: {missing}"


def test_every_concept_has_a_chinese_alias(build):
    by_concept: dict[str, set[LanguageEnum]] = {}
    for s in build.synonyms:
        by_concept.setdefault(s.concept_id, set()).add(s.lang)
    missing = [
        c.seed_key
        for c in build.concepts
        if LanguageEnum.zh not in by_concept.get(c.concept_id, set())
    ]
    assert not missing, f"缺少中文别名: {missing}"


def test_seed_covers_all_three_entity_types(build):
    types = {c.entity_type for c in build.concepts}
    assert types == {
        EntityTypeEnum.SUBSTANCE,
        EntityTypeEnum.TARGET,
        EntityTypeEnum.DISEASE,
    }


# ---------------------------------------------------------------- 可重放构建


def test_rebuild_produces_identical_ids(registry, seed_files, ambiguity, tmp_path: Path):
    """离线可重放：同样的输入必须产出同样的 ID。"""

    def run(dirname: str) -> tuple[dict[str, str], dict[str, str]]:
        d = tmp_path / dirname
        id_ledger = IdLedger(d / "ids.json", release="0.1.0")
        alias_ledger = SequenceLedger(d / "alias.json", prefix="HMDA")
        r = build_from_seed(
            seed_files,
            registry=registry,
            id_ledger=id_ledger,
            alias_ledger=alias_ledger,
            ambiguity=ambiguity,
        )
        id_ledger.save()
        alias_ledger.save()
        return (
            {c.seed_key: c.concept_id for c in r.concepts},
            {f"{s.concept_id}|{s.alias_norm}|{s.lang.value}": s.alias_id for s in r.synonyms},
        )

    first_concepts, first_aliases = run("a")
    second_concepts, second_aliases = run("b")
    assert first_concepts == second_concepts
    assert first_aliases == second_aliases


def test_incremental_build_reuses_existing_ids(registry, seed_files, ambiguity, tmp_path: Path):
    """在已有账本上重跑，ID 不得漂移 —— 否则历史 trace 会定位不到别名。"""
    id_path = tmp_path / "ids.json"
    alias_path = tmp_path / "alias.json"

    first_ledger = IdLedger(id_path, release="0.1.0")
    first_alias = SequenceLedger(alias_path, prefix="HMDA")
    first = build_from_seed(
        seed_files,
        registry=registry,
        id_ledger=first_ledger,
        alias_ledger=first_alias,
        ambiguity=ambiguity,
    )
    first_ledger.save()
    first_alias.save()

    second = build_from_seed(
        seed_files,
        registry=registry,
        id_ledger=IdLedger(id_path, release="0.2.0"),
        alias_ledger=SequenceLedger(alias_path, prefix="HMDA"),
        ambiguity=ambiguity,
    )

    assert {c.seed_key: c.concept_id for c in first.concepts} == {
        c.seed_key: c.concept_id for c in second.concepts
    }
