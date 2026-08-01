"""别名归一化（D2）。

索引侧与查询侧共用同一个函数，因此这里的每条断言都直接对应一次召回是否会丢。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import SynonymScopeEnum
from biomed_ontology.alias import (
    expansion_weight,
    generate_code_variants,
    is_indexed,
    normalize_alias,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("savolitinib", "savolitinib"),
        ("HMPL-504", "hmpl504"),
        ("HMPL 504", "hmpl504"),
        ("hmpl_504", "hmpl504"),
        ("AZD6094", "azd6094"),
        ("AZD-6094", "azd6094"),
        ("c-Met", "cmet"),
        ("c-MET", "cmet"),
        ("VEGFR-1", "vegfr1"),
        ("CSF-1R", "csf1r"),
        ("trifluridine/tipiracil", "trifluridinetipiracil"),
        ("  savolitinib  ", "savolitinib"),
        ("", ""),
    ],
)
def test_normalization(raw: str, expected: str):
    assert normalize_alias(raw) == expected


@pytest.mark.parametrize(
    "variant",
    ["PI3Kδ", "PI3K-delta", "PI3Kdelta", "PI3K delta", "PI3KΔ"],
)
def test_greek_letters_collapse_to_ascii(variant: str):
    """希腊字母与英文转写必须同键，否则 PI3Kδ 查不到 PI3K-delta 的文献。"""
    assert normalize_alias(variant) == "pi3kdelta"


def test_fullwidth_normalizes_to_halfwidth():
    """中文输入法常产出全角字符，不归一会造成静默漏召回。"""
    assert normalize_alias("ＨＭＰＬ－５０４") == normalize_alias("HMPL-504")


def test_chinese_punctuation_stripped():
    assert normalize_alias("非小细胞肺癌（NSCLC）") == "非小细胞肺癌nsclc"


def test_normalization_is_idempotent():
    for raw in ["HMPL-504", "PI3Kδ", "c-Met", "非小细胞肺癌"]:
        once = normalize_alias(raw)
        assert normalize_alias(once) == once


def test_distinct_concepts_do_not_collide():
    """归一化不能过度激进 —— 塌缩到一起就制造了假歧义。"""
    assert normalize_alias("VEGFR1") != normalize_alias("VEGFR2")
    assert normalize_alias("HMPL-504") != normalize_alias("HMPL-013")
    assert normalize_alias("PIK3CD") != normalize_alias("PIK3CA")


# ---------------------------------------------------------------- scope 语义


def test_exact_expands_at_full_weight():
    assert expansion_weight(SynonymScopeEnum.EXACT) == 1.0
    assert is_indexed(SynonymScopeEnum.EXACT)


def test_narrow_expands_downweighted():
    assert expansion_weight(SynonymScopeEnum.NARROW) == 0.8
    assert is_indexed(SynonymScopeEnum.NARROW)


@pytest.mark.parametrize("scope", [SynonymScopeEnum.BROAD, SynonymScopeEnum.RELATED])
def test_broad_and_related_never_expand_or_index(scope: SynonymScopeEnum):
    """把 related 当等价扩展会摧毁精确率，这是 D2 的核心约束。"""
    assert expansion_weight(scope) == 0.0
    assert not is_indexed(scope)


# ---------------------------------------------------------------- 变体生成


def test_code_variants_cover_separator_writings():
    variants = generate_code_variants("HMPL-504")
    assert {normalize_alias(v) for v in variants} == {"hmpl504"}
    assert "HMPL504" in variants
    assert "HMPL 504" in variants


def test_code_variants_skip_non_code_terms():
    """对普通词做变体扩展只会引入噪声。"""
    assert generate_code_variants("savolitinib") == set()
    assert generate_code_variants("非小细胞肺癌") == set()


def test_code_variants_exclude_the_input_itself():
    assert "HMPL-504" not in generate_code_variants("HMPL-504")
