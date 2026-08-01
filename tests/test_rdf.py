"""RDF 图层：命名图隔离、语句级溯源、SKOS 合法性。

许可隔离的测试重点是"越权时查不到"而不是"授权时查得到"——
后者失败只是功能缺失，前者失败是合规事故。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.licensing import named_graph_uri
from biomed_ontology.ontology.rdf import SPARQL_TEMPLATES, curie_to_iri

LICENSED = frozenset({"MOCK_LICENSED"})
SHAPES_DIR = Path(__file__).resolve().parents[1] / "schema" / "shapes"


def test_named_graph_uri_is_deterministic():
    a = named_graph_uri("PUBMED", LicenseTierEnum.TIER_0)
    b = named_graph_uri("PUBMED", LicenseTierEnum.TIER_0)
    assert a == b
    assert named_graph_uri("PATSNAP", LicenseTierEnum.TIER_3) != a


def test_tier3_graph_is_invisible_without_entitlement(kb):
    free = set(kb.graph.visible_graphs(frozenset()))
    paid = set(kb.graph.visible_graphs(LICENSED))
    assert paid - free, "有凭据应至少多看到一个命名图"
    assert all("tier_3" not in g for g in free)


def test_tier3_content_is_unreachable_without_entitlement(kb):
    """越权查询必须返回空集，而不是返回部分内容。

    这是整个许可隔离设计里唯一不能"差不多就行"的断言：
    漏一条 TIER_3 内容就是一次合规事故。
    """
    sparql = """
    PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
    SELECT ?d WHERE { GRAPH ?g { ?d hmd:sourceId "MOCK_LICENSED" . } }
    """
    assert kb.graph.query(sparql, entitlements=frozenset()) == []
    assert kb.graph.query(sparql, entitlements=LICENSED)


def test_query_cannot_escape_via_explicit_graph_clause(kb):
    """调用方自带 GRAPH <tier_3 图> 也不能绕过隔离。

    过滤靠的是重写 FROM NAMED 限定数据集，不是靠调用方不去写那个图名。
    """
    tier3 = next(g for g in kb.graph.visible_graphs(LICENSED) if "tier_3" in g)
    sparql = f"SELECT ?s ?p ?o WHERE {{ GRAPH <{tier3}> {{ ?s ?p ?o }} }}"
    assert kb.graph.query(sparql, entitlements=frozenset()) == []


def test_facts_are_reified_for_statement_level_provenance(kb):
    """事实要能按 fact_id 一次性拿回全部出处。

    行数会多于事实数：跨源合并的事实会在每个证据所在的命名图里各写一份，
    否则按源过滤时这条事实会跟着其中一个源一起消失。
    """
    sparql = """
    PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
    SELECT ?fid ?conf WHERE { GRAPH ?g { ?r hmd:factId ?fid ; hmd:confidence ?conf . } }
    """
    rows = kb.graph.query(sparql, entitlements=LICENSED)
    assert {r["fid"] for r in rows} == {f.fact_id for f in kb.facts}


def test_skos_broader_never_points_at_a_literal(kb):
    """skos:broader 的值域是 skos:Concept。

    早期把别名字符串写成了 broader 的宾语，SPARQL 不报错、
    层级扩展却会静默漏召回 —— 所以必须由测试守住。
    """
    sparql = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?s ?o WHERE { GRAPH ?g { ?s skos:broader|skos:narrower ?o } }
    """
    rows = kb.graph.query(sparql, entitlements=LICENSED)
    assert rows, "层级边不该为空"
    assert all(str(r["o"]).startswith("http") for r in rows)


@pytest.mark.parametrize("name", sorted(SPARQL_TEMPLATES))
def test_every_template_runs(kb, name):
    sparql = SPARQL_TEMPLATES[name]
    if "%(" in sparql:
        sparql = sparql % {"concept_uri": curie_to_iri("HMD:DIS:0000003")}
    kb.graph.query(sparql, entitlements=LICENSED)


def test_curie_to_iri_rejects_injection():
    with pytest.raises(ValueError):
        curie_to_iri("x> } DROP ALL #")
    with pytest.raises(ValueError):
        curie_to_iri("no-colon")
    assert curie_to_iri("HMD:SUB:0000001").startswith("https://")
    assert curie_to_iri("https://example.org/x") == "https://example.org/x"


def test_shacl_validation_conforms(kb):
    """用手写的投影约束校验，而非 gen-shacl 产物。

    生成的 shapes 是 closed 且面向 LinkML 实例形状，跟 SKOS/PROV 投影对不上。
    """
    shapes = SHAPES_DIR / "projection.shacl.ttl"
    report = kb.graph.validate_shacl(shapes)
    assert report.conforms, report.violations[:5]
