"""等价团构建（设计决策 D1）。

把 SSSOM exact_match 边看作无向图，连通分量即等价团，一个团对应一个内部概念。

连通分量对错误映射是零容忍的 —— 一条错误的 exact_match 会把两个不相关的概念焊死。
因此建团不只输出团，还必须输出冲突标记：
同一个权威源在一个团里出现两个不同 ID，几乎总是映射有误，必须人工介入。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from biomed_ontology._generated.hmd_concept import (
    LicenseTierEnum,
    MappingJustificationEnum,
    PredicateEnum,
)
from biomed_ontology.registry import SourceRegistry

__all__ = [
    "CliqueBuilder",
    "CliqueResult",
    "MappingEdge",
    "curie_prefix",
]

# 只有 exact_match 参与建团。close_match 语义上不保证可传递，
# 一旦纳入，A≈B、B≈C 会错误推出 A=C。
_CLIQUE_PREDICATES = frozenset({PredicateEnum.exact_match})


def curie_prefix(curie: str) -> str:
    return curie.split(":", 1)[0].lower()


@dataclass(frozen=True)
class MappingEdge:
    """一条 SSSOM 映射。"""

    subject_id: str
    object_id: str
    predicate: PredicateEnum
    justification: MappingJustificationEnum
    source: str
    confidence: float = 1.0
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0


@dataclass
class CliqueResult:
    """一个等价团。"""

    members: frozenset[str]
    primary_xref: str
    conflicts: list[str] = field(default_factory=list)
    contributing_sources: frozenset[str] = frozenset()
    max_license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    """团内成员的最高 tier —— 决定整个概念的可见性下限。"""

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


class CliqueBuilder:
    """按 registry 中声明的源角色决定 primary_xref 与冲突判定。"""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        min_confidence: float = 0.9,
        prefix_priority: list[str] | None = None,
    ) -> None:
        self.registry = registry
        self.min_confidence = min_confidence
        self._prefix_to_source: dict[str, str] = {}
        self._authoritative_prefixes: set[str] = set()
        for src in registry:
            if not src.bioregistry_prefix:
                continue
            prefix = src.bioregistry_prefix.lower()
            self._prefix_to_source[prefix] = src.id
            if src.is_authority:
                self._authoritative_prefixes.add(prefix)
        self.prefix_priority = [p.lower() for p in (prefix_priority or [])]

    # ------------------------------------------------------------------ 建团

    def build(self, edges: list[MappingEdge]) -> list[CliqueResult]:
        graph = self._to_graph(edges)
        results = [
            self._materialize(graph, component) for component in nx.connected_components(graph)
        ]
        return sorted(results, key=lambda c: c.primary_xref)

    def _to_graph(self, edges: list[MappingEdge]) -> nx.Graph:
        graph = nx.Graph()
        for e in edges:
            if e.predicate not in _CLIQUE_PREDICATES:
                continue
            if e.confidence < self.min_confidence:
                continue
            for node in (e.subject_id, e.object_id):
                if node not in graph:
                    graph.add_node(node, tier=LicenseTierEnum.TIER_0, sources=set())
            for node in (e.subject_id, e.object_id):
                graph.nodes[node]["sources"].add(e.source)
                if _tier_rank(e.license_tier) > _tier_rank(graph.nodes[node]["tier"]):
                    graph.nodes[node]["tier"] = e.license_tier
            graph.add_edge(e.subject_id, e.object_id, source=e.source)
        return graph

    def _materialize(self, graph: nx.Graph, component: set[str]) -> CliqueResult:
        members = frozenset(component)
        sources: set[str] = set()
        tier = LicenseTierEnum.TIER_0
        for node in component:
            sources |= graph.nodes[node]["sources"]
            if _tier_rank(graph.nodes[node]["tier"]) > _tier_rank(tier):
                tier = graph.nodes[node]["tier"]
        return CliqueResult(
            members=members,
            primary_xref=self._pick_primary(members),
            conflicts=self._detect_conflicts(members),
            contributing_sources=frozenset(sources),
            max_license_tier=tier,
        )

    # ------------------------------------------------------------------ 决策

    def _pick_primary(self, members: frozenset[str]) -> str:
        """选团代表：显式优先级 > 权威源 > 字典序。

        字典序兜底不是随意选择 —— 它保证同一组成员在任何一次重建中都选出同一个代表。
        """

        def rank(curie: str) -> tuple[int, int, str]:
            prefix = curie_prefix(curie)
            if prefix in self.prefix_priority:
                return (0, self.prefix_priority.index(prefix), curie)
            if prefix in self._authoritative_prefixes:
                return (1, 0, curie)
            return (2, 0, curie)

        return min(members, key=rank)

    def _detect_conflicts(self, members: frozenset[str]) -> list[str]:
        by_prefix: dict[str, list[str]] = defaultdict(list)
        for m in members:
            by_prefix[curie_prefix(m)].append(m)

        conflicts = []
        for prefix, ids in sorted(by_prefix.items()):
            if len(ids) > 1 and prefix in self._authoritative_prefixes:
                source_id = self._prefix_to_source.get(prefix, prefix)
                conflicts.append(
                    f"权威源 {source_id} 在同一等价团中出现 {len(ids)} 个不同 ID: {sorted(ids)}"
                )
        return conflicts


_TIER_RANK = {
    LicenseTierEnum.TIER_0: 0,
    LicenseTierEnum.TIER_1: 1,
    LicenseTierEnum.TIER_2: 2,
    LicenseTierEnum.TIER_3: 3,
}


def _tier_rank(tier: LicenseTierEnum) -> int:
    return _TIER_RANK[tier]
