"""等价团构建（D1）。

一条错误的 exact_match 会把两个不相关概念永久焊死，
因此冲突检测的测试比建团本身更重要。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import (
    LicenseTierEnum,
    MappingJustificationEnum,
    PredicateEnum,
)
from biomed_ontology.ontology.clique import CliqueBuilder, MappingEdge


def edge(
    subject: str,
    obj: str,
    *,
    predicate: PredicateEnum = PredicateEnum.exact_match,
    confidence: float = 1.0,
    source: str = "SEED_INTERNAL",
    tier: LicenseTierEnum = LicenseTierEnum.TIER_0,
) -> MappingEdge:
    return MappingEdge(
        subject_id=subject,
        object_id=obj,
        predicate=predicate,
        justification=MappingJustificationEnum.LexicalMatching,
        source=source,
        confidence=confidence,
        license_tier=tier,
    )


@pytest.fixture
def builder(registry) -> CliqueBuilder:
    return CliqueBuilder(registry)


def test_transitive_edges_form_one_clique(builder: CliqueBuilder):
    cliques = builder.build([edge("unii:A", "chembl:B"), edge("chembl:B", "drugcentral:C")])
    assert len(cliques) == 1
    assert cliques[0].members == frozenset({"unii:A", "chembl:B", "drugcentral:C"})


def test_disconnected_edges_stay_separate(builder: CliqueBuilder):
    cliques = builder.build([edge("unii:A", "chembl:B"), edge("unii:X", "chembl:Y")])
    assert len(cliques) == 2


def test_close_match_does_not_join_cliques(builder: CliqueBuilder):
    """close_match 不可传递 —— 纳入建团会由 A≈B、B≈C 错误推出 A=C。"""
    cliques = builder.build(
        [
            edge("unii:A", "chembl:B"),
            edge("chembl:B", "chembl:C", predicate=PredicateEnum.close_match),
        ]
    )
    assert len(cliques) == 1
    assert "chembl:C" not in cliques[0].members


def test_low_confidence_edges_excluded(builder: CliqueBuilder):
    cliques = builder.build(
        [edge("unii:A", "chembl:B"), edge("chembl:B", "chembl:C", confidence=0.4)]
    )
    assert cliques[0].members == frozenset({"unii:A", "chembl:B"})


def test_authoritative_source_conflict_is_flagged(builder: CliqueBuilder):
    """同一权威源在一个团里出现两个 ID，几乎总是映射有误。"""
    cliques = builder.build([edge("hgnc:1", "uniprot:P1"), edge("uniprot:P1", "hgnc:2")])
    assert len(cliques) == 1
    assert cliques[0].has_conflict
    assert "HGNC" in cliques[0].conflicts[0]


def test_non_authoritative_duplicates_are_not_flagged(builder: CliqueBuilder):
    """ChEMBL 是校验源，同团多 ID 属正常（盐型、不同批次登记）。"""
    cliques = builder.build([edge("chembl:1", "unii:A"), edge("unii:A", "chembl:2")])
    assert not cliques[0].has_conflict


def test_primary_xref_prefers_authoritative_prefix(builder: CliqueBuilder):
    cliques = builder.build([edge("chembl:9", "unii:A")])
    assert cliques[0].primary_xref == "unii:A"


def test_explicit_prefix_priority_overrides_role(registry):
    builder = CliqueBuilder(registry, prefix_priority=["chembl.compound", "unii"])
    cliques = builder.build([edge("chembl.compound:9", "unii:A")])
    assert cliques[0].primary_xref == "chembl.compound:9"


def test_primary_xref_is_deterministic(builder: CliqueBuilder):
    """字典序兜底保证同一组成员在任何一次重建中选出同一代表。"""
    forward = builder.build([edge("ncit:X", "mesh:Y"), edge("mesh:Y", "mesh:Z")])
    backward = builder.build([edge("mesh:Z", "mesh:Y"), edge("mesh:Y", "ncit:X")])
    assert forward[0].primary_xref == backward[0].primary_xref


def test_clique_inherits_most_restrictive_tier(builder: CliqueBuilder):
    """团内混入受限源后，整个概念的可见性下限由最严的那个源决定。"""
    cliques = builder.build(
        [
            edge("unii:A", "chembl:B"),
            edge("chembl:B", "meddra:C", source="MEDDRA", tier=LicenseTierEnum.TIER_3),
        ]
    )
    assert cliques[0].max_license_tier is LicenseTierEnum.TIER_3


def test_contributing_sources_are_tracked(builder: CliqueBuilder):
    cliques = builder.build(
        [edge("unii:A", "chembl:B", source="UNII"), edge("chembl:B", "x:C", source="CHEMBL")]
    )
    assert cliques[0].contributing_sources == frozenset({"UNII", "CHEMBL"})


def test_empty_input_yields_no_cliques(builder: CliqueBuilder):
    assert builder.build([]) == []
