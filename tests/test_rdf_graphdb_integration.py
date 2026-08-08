"""真实 GraphDB 上的 KB 许可命名图隔离（需 task foundation:up）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.licensing import named_graph_uri
from biomed_ontology.ontology.rdf import GraphStore

pytestmark = pytest.mark.integration

SRC_FREE = "TEST_RDF_FREE"
SRC_PAID = "TEST_RDF_PAID"


@pytest.fixture(scope="module")
def gdb() -> GraphDbClient:
    client = GraphDbClient.from_settings()
    if not client.health():
        pytest.skip("GraphDB 不可达；请先 task foundation:up")
    return client


@pytest.fixture
def store(gdb: GraphDbClient) -> GraphStore:
    gs = GraphStore(client=gdb)
    yield gs
    # 清理本测专用命名图与 meta 条目
    for src, tier in (
        (SRC_FREE, LicenseTierEnum.TIER_0),
        (SRC_PAID, LicenseTierEnum.TIER_3),
    ):
        uri = named_graph_uri(src, tier)
        try:
            gdb.clear_graph(uri)
            gdb.update(
                f"DELETE WHERE {{ GRAPH <https://w3id.org/asliva/biomed-ontology/graph/meta> "
                f"{{ <{uri}> ?p ?o }} }}"
            )
        except Exception:
            pass


def _mini_concept(cid: str, source_label: str):
    return SimpleNamespace(
        concept_id=cid,
        entity_type=SimpleNamespace(value="SUBSTANCE"),
        preferred_label_en=source_label,
        preferred_label_zh=None,
        definition=None,
        license_tier=LicenseTierEnum.TIER_0,
        parents=[],
        links=[],
    )


def test_tier3_unreachable_without_entitlement(store: GraphStore):
    free_uri = store.load_concepts(
        [_mini_concept("HMD:ENT:DC:test_free", "free-drug")],
        [],
        source_id=SRC_FREE,
        tier=LicenseTierEnum.TIER_0,
    )
    paid_uri = store.register_graph(SRC_PAID, LicenseTierEnum.TIER_3)
    # 在付费图写入可识别三元组
    store.client.replace_graph(
        paid_uri,
        f"""@prefix hmd: <https://w3id.org/asliva/biomed-ontology/> .
<{paid_uri}> hmd:sourceId "{SRC_PAID}" .
""",
    )
    # meta 已由 register_graph 写入
    assert free_uri in store.visible_graphs(frozenset())
    assert paid_uri not in store.visible_graphs(frozenset())
    assert paid_uri in store.visible_graphs(frozenset({SRC_PAID}))

    sparql = f"""
    PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
    SELECT ?s WHERE {{ GRAPH ?g {{ ?s hmd:sourceId "{SRC_PAID}" . }} }}
    """
    assert store.query(sparql, entitlements=frozenset()) == []
    assert store.query(sparql, entitlements=frozenset({SRC_PAID}))

    # 显式 GRAPH 子句也不能绕过 FROM NAMED 裁剪
    escape = f"SELECT ?s ?p ?o WHERE {{ GRAPH <{paid_uri}> {{ ?s ?p ?o }} }}"
    assert store.query(escape, entitlements=frozenset()) == []
