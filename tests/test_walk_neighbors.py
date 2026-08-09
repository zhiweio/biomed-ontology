"""walk_neighbors 纯函数：衰减、跨类型一跳、min_weight。"""

from __future__ import annotations

from pytest import approx

from biomed_ontology.ontology.links import walk_neighbors


def _adj(edges: dict[str, list[tuple[str, str]]]):
    return lambda cids: {c: edges.get(c, []) for c in cids}


def test_relation_decay_applied_per_hop():
    edges = {
        "A": [("B", "narrower")],
        "B": [("C", "narrower")],
    }
    hits = {n.concept_id: n for n in walk_neighbors(["A"], _adj(edges), max_hops=2)}
    assert hits["B"].weight == 0.8
    assert hits["C"].weight == approx(0.64)


def test_cross_type_hop_at_most_once():
    edges = {
        "DRUG": [("TARGET", "has_target")],
        "TARGET": [("OTHER", "targeted_by")],
    }
    reached = walk_neighbors(["DRUG"], _adj(edges), max_hops=2)
    assert {n.concept_id for n in reached} == {"TARGET"}


def test_min_weight_stops_dilution():
    edges = {"A": [("B", "targeted_by")]}  # decay 0.55
    assert walk_neighbors(["A"], _adj(edges), max_hops=1, min_weight=0.6) == []


def test_keeps_highest_weight_path():
    edges = {
        "A": [("X", "narrower"), ("B", "broader")],
        "B": [("X", "narrower")],
    }
    # A→X 直接 narrower = 0.8；A→B→X = 0.7*0.8 = 0.56
    hits = walk_neighbors(["A"], _adj(edges), max_hops=2)
    by_id = {n.concept_id: n for n in hits}
    assert by_id["X"].weight == 0.8
    assert by_id["X"].hops == 1
