"""概念图的 search-around：从一个概念出发，沿类型化链接走到相关概念。

这是本体在检索里真正区别于同义词表的地方。层级扩展（`skos:broader`）只能
在同一类实体内部上下走 —— 查"肺癌"能带出"肺腺癌"，但查"VEGFR2 抑制剂"
一步也走不到呋喹替尼，因为靶点没有下位概念。真实的问题几乎都是跨类型的：
从靶点找药、从药找适应症、从适应症找在研管线。

三条设计约束：

**反向边与正向边同等重要。** 种子里只写了药→靶点，但"MET 抑制剂有哪些"
问的是反过来那一路。所以这里把每条边都建双向邻接，用不同谓词区分方向，
让衰减权重可以给两个方向配不同的值。

**每种关系有自己的衰减。** 上位/下位是"同一个东西的粗细粒度"，语义距离小；
药→靶点是"两个不同的东西恰好相关"，距离大。用同一个 0.8 会让二跳的靶点
盖过一跳的下位概念，那不是任何人查询时的意图。

**一条路径上最多一次跨类型跳。** 层级边可以自由复合 —— 孙子概念仍然是
祖父概念的一个特化，这是 `skos:broader` 的传递性，本来就成立。跨类型边不行：
`has_target ∘ targeted_by` 展开是"与某药共享靶点的另一些药"，
那是**竞品关系**，不是"回答同一个问题"。一篇讲另一种 MET 抑制剂的文章，
对"赛沃替尼疗效如何"这个查询没有价值，但它在图上离查询只有两跳。
两种关系复合出来的东西不该继承两段权重的乘积 —— 它是一个全新的、
弱得多的关系，需要单独论证才能用。这里的处置是直接不走。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.ingest.seed import BuiltConcept

__all__ = ["RELATION_DECAY", "LinkIndex", "Neighbor"]

# 谓词 → 每跳的权重衰减。
#
# 层级边 0.8 沿用图通道原有取值（"一层之外的关联仍然有用，但不该盖过字面直击"）。
# 跨类型边给 0.65：药与它的靶点确实相关，但一篇只提到 MET 的综述，
# 对"赛沃替尼疗效"这个查询的价值明显低于一篇直接讲赛沃替尼的文章。
# 反向边比正向再低一档：从靶点出发能到的药往往有好几个（MET 抑制剂不止一种），
# 扇出越大，单条边携带的信息越少。
RELATION_DECAY: dict[str, float] = {
    "narrower": 0.8,
    "broader": 0.7,
    "has_target": 0.65,
    "targeted_by": 0.55,
    "treats": 0.65,
    "treated_by": 0.55,
}

# 种子链接的反向谓词。正向名字来自 `ingest.seed.LINK_PREDICATES`，
# 这里只登记它的反面 —— 两处都写一遍正向名字，迟早会有一处拼错。
_INVERSE = {"has_target": "targeted_by", "treats": "treated_by"}

# 可自由复合的关系。层级是传递的（孙子仍是祖父的特化），跨类型边不是。
_HIERARCHY = frozenset({"broader", "narrower"})


@dataclass(frozen=True)
class Neighbor:
    """一次遍历命中的概念。`weight` 已经把沿途每一跳的衰减乘进去了。"""

    concept_id: str
    hops: int
    predicate: str
    weight: float


class LinkIndex:
    """概念之间全部边的双向邻接表。

    与 `Normalizer._children` 的关系：那份只有层级、只有向下一个方向，
    是 `descendants()` / `expand()` 的底座。这份是层级 + 类型化链接、双向，
    专供检索期的 search-around。两份没有合并是因为它们回答不同的问题 ——
    别名扩展只该沿层级走（下位概念的别名仍是同一个东西的名字），
    而检索召回该沿全部关系走。
    """

    def __init__(self, concepts: list[BuiltConcept]) -> None:
        self._edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
        known = {c.concept_id for c in concepts}
        key_to_id = {c.seed_key: c.concept_id for c in concepts}

        def add(src: str, dst: str, predicate: str) -> None:
            self._edges[src].append((dst, predicate))
            self._edges[dst].append((src, _INVERSE[predicate]))

        for c in concepts:
            for p in c.parents:
                pid = key_to_id.get(p, p)
                if pid in known:
                    # 层级边的两个方向都要有：查上位能带出下位（原有行为），
                    # 查下位也该能带出上位（"肺腺癌"的证据里有一部分写在"肺癌"名下）。
                    self._edges[c.concept_id].append((pid, "broader"))
                    self._edges[pid].append((c.concept_id, "narrower"))
            for link in c.links:
                if link.object_id in known and link.predicate in _INVERSE:
                    add(c.concept_id, link.object_id, link.predicate)

    def neighbors(
        self,
        seeds: list[str] | set[str],
        *,
        max_hops: int = 2,
        predicates: frozenset[str] | None = None,
        min_weight: float = 0.1,
    ) -> list[Neighbor]:
        """从 `seeds` 出发做带权 BFS，返回可达概念（不含种子自身）。

        同一个概念经由多条路径到达时保留**权重最高**的那条 —— 它是最短/最强的
        解释，也是唯一一个能写进 explain 里而不引起误解的。

        路径上最多允许一次跨类型跳，理由见模块头。层级边不受此限。

        `min_weight` 是硬止损：不设的话，图上任意两个概念之间几乎总有路径，
        遍历会退化成"返回全部概念"，而那正是图通道判别力被稀释的老毛病。
        """
        seen: dict[str, Neighbor] = {}
        # 状态是 (概念, 这条路径上是否已经跨过类型)，不是单纯的概念 ——
        # 同一个概念经由"两跳层级"和"一跳跨类型"到达，后续能走的边不一样。
        frontier: dict[tuple[str, bool], float] = {(s, False): 1.0 for s in seeds}
        origins = set(seeds)

        for hop in range(1, max_hops + 1):
            nxt: dict[tuple[str, bool], float] = {}
            for (cid, crossed), carried in frontier.items():
                for dst, predicate in self._edges.get(cid, ()):
                    if dst in origins:
                        continue
                    if predicates is not None and predicate not in predicates:
                        continue
                    typed = predicate not in _HIERARCHY
                    if typed and crossed:
                        continue
                    weight = carried * RELATION_DECAY.get(predicate, 0.5)
                    if weight < min_weight:
                        continue
                    prior = seen.get(dst)
                    if prior is None or weight > prior.weight:
                        seen[dst] = Neighbor(dst, hop, predicate, weight)
                    # 继续往外扩时带的是该状态当前最好的权重，
                    # 否则同一个节点会按"第一次到达的那条路"决定后续所有分支。
                    key = (dst, crossed or typed)
                    if weight > nxt.get(key, 0.0):
                        nxt[key] = weight
            frontier = nxt
            if not frontier:
                break

        return sorted(seen.values(), key=lambda n: (-n.weight, n.concept_id))

    def degree(self, concept_id: str) -> int:
        return len(self._edges.get(concept_id, ()))
