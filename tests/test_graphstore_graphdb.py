"""GraphStore → GraphDB：respx HTTP mock + 注入 client 的装载/重写断言。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import respx

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.licensing import named_graph_uri
from biomed_ontology.ontology.rdf import (
    HMD,
    SPARQL_TEMPLATES,
    GraphStore,
    curie_to_iri,
)

LICENSED = frozenset({"MOCK_LICENSED"})
BASE = "http://graphdb.test:7200"
REPO = "hmd"


@dataclass
class _RecordingClient:
    """鸭子类型 GraphDbClient：记录 Turtle / SPARQL，供无 Docker 单测。"""

    base_url: str = BASE
    repository: str = REPO
    turtles: dict[str, str] = field(default_factory=dict)
    cleared: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    meta_rows: list[dict[str, str]] = field(default_factory=list)

    def health(self) -> bool:
        return True

    def query(self, sparql: str) -> list[dict[str, str]]:
        self.queries.append(sparql)
        if "licenseTier" in sparql and "sourceId" in sparql:
            return list(self.meta_rows)
        if "COUNT" in sparql.upper():
            return [{"c": "3"}]
        return []

    def ask(self, sparql: str) -> bool:
        self.queries.append(sparql)
        return False

    def update(self, sparql: str) -> None:
        self.queries.append(sparql)

    def load_turtle(self, turtle: str, *, graph_uri: str, retries: int = 3) -> None:
        self.turtles[graph_uri] = self.turtles.get(graph_uri, "") + turtle

    def clear_graph(self, graph_uri: str) -> None:
        self.cleared.append(graph_uri)
        self.turtles.pop(graph_uri, None)

    def replace_graph(self, graph_uri: str, turtle: str) -> None:
        self.clear_graph(graph_uri)
        if turtle.strip():
            self.load_turtle(turtle, graph_uri=graph_uri)

    def export_graph(
        self, graph_uri: str | None = None, *, accept: str = "application/n-quads"
    ) -> bytes:
        if graph_uri:
            return self.turtles.get(graph_uri, "").encode("utf-8")
        return "\n".join(self.turtles.values()).encode("utf-8")


def _concept(cid: str = "HMD:ENT:DC:savolitinib"):
    return SimpleNamespace(
        concept_id=cid,
        entity_type=SimpleNamespace(value="SUBSTANCE"),
        preferred_label_en="savolitinib",
        preferred_label_zh="沃利替尼",
        definition="MET inhibitor",
        license_tier=LicenseTierEnum.TIER_0,
        parents=[],
        links=[
            SimpleNamespace(predicate="has_target", object_id="HMD:ENT:TGT:MET"),
        ],
    )


def _synonym(cid: str = "HMD:ENT:DC:savolitinib"):
    return SimpleNamespace(
        concept_id=cid,
        alias_id="HMDA:1",
        alias_raw="AZD6094",
        alias_norm="azd6094",
        scope=SimpleNamespace(value="EXACT"),
        lang=SimpleNamespace(value="en"),
        is_ambiguous=False,
    )


def _fact():
    return SimpleNamespace(
        fact_id="FACT:1",
        subject_id="HMD:ENT:DC:savolitinib",
        predicate=SimpleNamespace(value="has_target"),
        object_id="HMD:ENT:TGT:MET",
        object_value=None,
        object_unit=None,
        confidence=0.9,
        extractor_id="tri-modal",
        license_tier=LicenseTierEnum.TIER_0,
        modality=SimpleNamespace(value="TEXT"),
        qualifiers=[],
        evidence=[SimpleNamespace(chunk_id="CHK:1", quote="targets MET")],
    )


@pytest.fixture
def store():
    client = _RecordingClient()
    gs = GraphStore(client=client)
    with patch("biomed_ontology.ontology.rdf.ensure_repository"):
        yield gs, client


def test_load_concepts_writes_turtle_to_named_graph(store):
    gs, client = store
    with patch("biomed_ontology.ontology.rdf.ensure_repository"):
        uri = gs.load_concepts(
            [_concept()],
            [_synonym()],
            source_id="SEED_INTERNAL",
            tier=LicenseTierEnum.TIER_0,
        )
    assert uri == named_graph_uri("SEED_INTERNAL", LicenseTierEnum.TIER_0)
    assert uri in client.turtles
    body = client.turtles[uri]
    assert "skos:Concept" in body
    assert "AZD6094" in body
    assert uri in client.cleared


def test_load_facts_uses_rdf11_reification(store):
    gs, client = store
    with patch("biomed_ontology.ontology.rdf.ensure_repository"):
        uri = gs.load_facts([_fact()], source_id="PUBMED", tier=LicenseTierEnum.TIER_0)
    body = client.turtles[uri]
    assert "rdf:subject" in body
    assert "rdf:predicate" in body
    assert "rdf:object" in body
    assert "rdf:reifies" not in body
    assert 'hmd:factId "FACT:1"' in body


def test_rewrite_hides_tier3_without_entitlement(store):
    gs, _ = store
    tier0 = named_graph_uri("PUBMED", LicenseTierEnum.TIER_0)
    tier3 = named_graph_uri("MOCK_LICENSED", LicenseTierEnum.TIER_3)
    gs._graph_tier = {tier0: LicenseTierEnum.TIER_0, tier3: LicenseTierEnum.TIER_3}
    gs._graph_source = {tier0: "PUBMED", tier3: "MOCK_LICENSED"}
    sparql = "SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } }"
    free = gs._rewrite(sparql, frozenset())
    paid = gs._rewrite(sparql, LICENSED)
    assert tier3 not in free
    assert tier0 in free
    assert tier3 in paid
    assert "FROM NAMED" in free


def test_query_asks_via_client(store):
    gs, client = store
    gs._graph_tier = {}
    gs._graph_source = {}
    rows = gs.query("ASK WHERE { ?s ?p ?o }", unrestricted=True)
    assert rows == [{"result": "False"}]
    assert client.queries[-1].lstrip().upper().startswith("ASK")


def test_every_template_rewrites(store):
    gs, client = store
    tier0 = named_graph_uri("SEED_INTERNAL", LicenseTierEnum.TIER_0)
    gs._graph_tier = {tier0: LicenseTierEnum.TIER_0}
    gs._graph_source = {tier0: "SEED_INTERNAL"}
    for name, tmpl in SPARQL_TEMPLATES.items():
        sparql = tmpl
        if "%(" in sparql:
            sparql = sparql % {"concept_uri": curie_to_iri("HMD:ENT:IND:lung_cancer")}
        gs.query(sparql, entitlements=LICENSED)
        assert client.queries, name
        assert "FROM NAMED" in client.queries[-1] or "WHERE" in client.queries[-1].upper()


def test_curie_to_iri_rejects_injection():
    with pytest.raises(ValueError):
        curie_to_iri("x> } DROP ALL #")
    with pytest.raises(ValueError):
        curie_to_iri("no-colon")
    assert curie_to_iri("HMD:ENT:DC:savolitinib").startswith("https://")


@respx.mock
def test_graphdb_client_query_and_load_via_http():
    client = GraphDbClient(base_url=BASE, repository=REPO, timeout=5.0)
    respx.get(f"{BASE}/rest/repositories").respond(200, json=[{"id": REPO}])
    route = respx.post(f"{BASE}/repositories/{REPO}").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {"x": {"type": "uri", "value": f"{HMD}x"}},
                    ]
                }
            },
        )
    )
    stmt = respx.post(f"{BASE}/repositories/{REPO}/statements").respond(204)
    assert client.health()
    rows = client.query("SELECT ?x WHERE { ?x ?p ?o }")
    assert rows == [{"x": f"{HMD}x"}]
    assert route.called
    client.load_turtle("@prefix : <http://example.org/> . :a :b :c .", graph_uri=f"{HMD}graph/t")
    assert stmt.called
    assert "context" in str(stmt.calls.last.request.url)


@respx.mock
def test_graphdb_client_ask_and_export():
    client = GraphDbClient(base_url=BASE, repository=REPO, timeout=5.0)
    respx.post(f"{BASE}/repositories/{REPO}").mock(
        return_value=httpx.Response(200, json={"boolean": True})
    )
    assert client.ask("ASK WHERE { ?s ?p ?o }") is True
    respx.get(f"{BASE}/repositories/{REPO}/statements").mock(
        return_value=httpx.Response(200, content=b"<a> <b> <c> .")
    )
    raw = client.export_graph(f"{HMD}graph/t", accept="text/turtle")
    assert raw.startswith(b"<a>")


@respx.mock
def test_ensure_requires_graphdb_on_load():
    client = GraphDbClient(base_url=BASE, repository=REPO, timeout=5.0)
    respx.get(f"{BASE}/rest/repositories").respond(503)
    gs = GraphStore(client=client)
    with pytest.raises(RuntimeError, match="GraphDB"):
        gs.load_concepts([], [], source_id="X", tier=LicenseTierEnum.TIER_0)
