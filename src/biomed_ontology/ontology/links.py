"""概念图 search-around 的行走策略（边存储在 GraphDB，见 neighborhood.py）。

这是本体在检索里真正区别于同义词表的地方。层级扩展（`skos:broader`）只能
在同一类实体内部上下走 —— 查"肺癌"能带出"肺腺癌"，但查"VEGFR2 抑制剂"
一步也走不到呋喹替尼。真实问题几乎都是跨类型的。

三条设计约束：

**反向边与正向边同等重要。** 种子只写药→靶点，查询常问反过来那一路。
邻接查询侧合成反向谓词，衰减可按方向区分。

**每种关系有自己的衰减。** 上位/下位语义距离小；药→靶点距离大。

**一条路径上最多一次跨类型跳。** `has_target ∘ targeted_by` 是竞品关系，
不是"回答同一个问题"——直接不走。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "HIERARCHY_PREDICATES",
    "INVERSE_PREDICATES",
    "RELATION_DECAY",
    "Neighbor",
    "walk_neighbors",
]

RELATION_DECAY: dict[str, float] = {
    "narrower": 0.8,
    "broader": 0.7,
    "has_target": 0.65,
    "targeted_by": 0.55,
    "treats": 0.65,
    "treated_by": 0.55,
}

INVERSE_PREDICATES: dict[str, str] = {"has_target": "targeted_by", "treats": "treated_by"}

HIERARCHY_PREDICATES: frozenset[str] = frozenset({"broader", "narrower"})

AdjacencyFn = Callable[[set[str]], dict[str, list[tuple[str, str]]]]


@dataclass(frozen=True)
class Neighbor:
    """一次遍历命中的概念。`weight` 已经把沿途每一跳的衰减乘进去了。"""

    concept_id: str
    hops: int
    predicate: str
    weight: float


def walk_neighbors(
    seeds: list[str] | set[str],
    adjacency: AdjacencyFn,
    *,
    max_hops: int = 2,
    predicates: frozenset[str] | None = None,
    min_weight: float = 0.1,
) -> list[Neighbor]:
    """从 `seeds` 出发做带权 BFS，返回可达概念（不含种子自身）。

    `adjacency(cids)` 返回 `{src: [(dst, predicate), ...]}`，由 GraphDB 或测试桩提供。
    同一概念多路径到达时保留权重最高的那条；路径上最多一次跨类型跳。
    """
    seen: dict[str, Neighbor] = {}
    frontier: dict[tuple[str, bool], float] = {(s, False): 1.0 for s in seeds}
    origins = set(seeds)

    for hop in range(1, max_hops + 1):
        if not frontier:
            break
        nodes = {cid for cid, _ in frontier}
        edges = adjacency(nodes)
        nxt: dict[tuple[str, bool], float] = {}
        for (cid, crossed), carried in frontier.items():
            for dst, predicate in edges.get(cid, ()):
                if dst in origins:
                    continue
                if predicates is not None and predicate not in predicates:
                    continue
                typed = predicate not in HIERARCHY_PREDICATES
                if typed and crossed:
                    continue
                weight = carried * RELATION_DECAY.get(predicate, 0.5)
                if weight < min_weight:
                    continue
                prior = seen.get(dst)
                if prior is None or weight > prior.weight:
                    seen[dst] = Neighbor(dst, hop, predicate, weight)
                key = (dst, crossed or typed)
                if weight > nxt.get(key, 0.0):
                    nxt[key] = weight
        frontier = nxt

    return sorted(seen.values(), key=lambda n: (-n.weight, n.concept_id))
