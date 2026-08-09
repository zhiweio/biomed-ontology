"""GraphDB 邻接：企业概念 search-around 的边权威。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from biomed_ontology.ontology.links import (
    INVERSE_PREDICATES,
    Neighbor,
    walk_neighbors,
)
from biomed_ontology.ontology.rdf import HMD, SKOS, curie_to_iri

if TYPE_CHECKING:
    from biomed_ontology.ontology.rdf import GraphStore

__all__ = ["ConceptNeighborhood", "GraphDbNeighborhood", "NullNeighborhood", "iri_to_curie"]

_TYPED_FORWARD = tuple(INVERSE_PREDICATES.keys())


def iri_to_curie(iri: str) -> str:
    """装载用 IRI → CURIE（与 ``rdf._curie_iri`` 对称）。"""
    if iri.startswith(HMD):
        local = iri[len(HMD) :]
        # HMD_ENT_DRUG_X → HMD:ENT:DRUG_X（前两段 `_` 还原为 `:`）
        if local.startswith("HMD_"):
            rest = local[4:]
            head, _, tail = rest.partition("_")
            return f"HMD:{head}:{tail}" if head and tail else local
        return local
    marker = "bioregistry.io/"
    if marker in iri:
        return iri.split(marker, 1)[1]
    return iri


class ConceptNeighborhood(Protocol):
    def neighbors(
        self,
        seeds: list[str] | set[str],
        *,
        max_hops: int = 2,
        predicates: frozenset[str] | None = None,
        min_weight: float = 0.1,
    ) -> list[Neighbor]: ...


class NullNeighborhood:
    """无边邻域：仅用于不需要 GRAPH 通道的装配（如 ``hmd index`` 写行）。"""

    def neighbors(
        self,
        seeds: list[str] | set[str],
        *,
        max_hops: int = 2,
        predicates: frozenset[str] | None = None,
        min_weight: float = 0.1,
    ) -> list[Neighbor]:
        return []


class GraphDbNeighborhood:
    """从 GraphDB 读一跳出/入边，进程内跑 ``walk_neighbors``。"""

    def __init__(self, store: GraphStore, *, entitlements: frozenset[str] | None = None) -> None:
        self._store = store
        self._entitlements = entitlements or frozenset()

    def neighbors(
        self,
        seeds: list[str] | set[str],
        *,
        max_hops: int = 2,
        predicates: frozenset[str] | None = None,
        min_weight: float = 0.1,
    ) -> list[Neighbor]:
        return walk_neighbors(
            seeds,
            self.adjacency_many,
            max_hops=max_hops,
            predicates=predicates,
            min_weight=min_weight,
        )

    def adjacency_many(self, cids: set[str]) -> dict[str, list[tuple[str, str]]]:
        if not cids:
            return {}
        iris = []
        iri_to_src: dict[str, str] = {}
        for cid in cids:
            try:
                iri = curie_to_iri(cid) if ":" in cid else cid
            except ValueError:
                continue
            iris.append(iri)
            iri_to_src[iri] = cid
        if not iris:
            return {}

        values = " ".join(f"<{i}>" for i in iris)
        unions: list[str] = [
            f"""
            {{ GRAPH ?g {{ ?s <{SKOS}broader> ?o }}
              BIND("broader" AS ?p) }}
            """,
            f"""
            {{ GRAPH ?g {{ ?o <{SKOS}broader> ?s }}
              BIND("narrower" AS ?p) }}
            """,
        ]
        for fwd in _TYPED_FORWARD:
            inv = INVERSE_PREDICATES[fwd]
            unions.append(
                f"""
                {{ GRAPH ?g {{ ?s hmd:{fwd} ?o }}
                  BIND("{fwd}" AS ?p) }}
                """
            )
            unions.append(
                f"""
                {{ GRAPH ?g {{ ?o hmd:{fwd} ?s }}
                  BIND("{inv}" AS ?p) }}
                """
            )
        sparql = f"""
        PREFIX hmd: <{HMD}>
        SELECT ?s ?p ?o WHERE {{
          VALUES ?s {{ {values} }}
          {" UNION ".join(unions)}
        }}
        """
        try:
            rows = self._store.query(sparql, entitlements=self._entitlements, unrestricted=True)
        except Exception as exc:
            raise RuntimeError(f"GraphDB 邻接查询失败：{exc}") from exc

        out: dict[str, list[tuple[str, str]]] = {cid: [] for cid in cids}
        for row in rows:
            s_iri = row.get("s") or ""
            o_iri = row.get("o") or ""
            pred = row.get("p") or ""
            src = iri_to_src.get(s_iri) or iri_to_curie(s_iri)
            dst = iri_to_curie(o_iri)
            if not src or not dst or not pred:
                continue
            out.setdefault(src, []).append((dst, pred))
        return out
