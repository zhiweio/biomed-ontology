"""RDF 图层：命名图 URI 与 CURIE 绑定（无 GraphDB）。

许可隔离 / 装载 / 重写见 ``test_graphstore_graphdb.py``（respx / 注入 client）；
真实 GraphDB 见 ``test_rdf_graphdb_integration.py``（``pytest -m integration``）。
"""

from __future__ import annotations

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.licensing import named_graph_uri
from biomed_ontology.ontology.rdf import curie_to_iri


def test_named_graph_uri_is_deterministic():
    a = named_graph_uri("PUBMED", LicenseTierEnum.TIER_0)
    b = named_graph_uri("PUBMED", LicenseTierEnum.TIER_0)
    assert a == b
    assert named_graph_uri("PATSNAP", LicenseTierEnum.TIER_3) != a
    assert "tier_0" in a or "TIER_0".lower() in a


def test_curie_to_iri_rejects_injection():
    import pytest

    with pytest.raises(ValueError):
        curie_to_iri("x> } DROP ALL #")
    with pytest.raises(ValueError):
        curie_to_iri("no-colon")
    assert curie_to_iri("HMD:ENT:DC:savolitinib").startswith("https://")
    assert curie_to_iri("https://example.org/x") == "https://example.org/x"
