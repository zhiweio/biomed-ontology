"""目录装配不经 KnowledgeBase / pipeline。"""

from __future__ import annotations

from pathlib import Path

from biomed_ontology.contracts import ConceptCatalog, GraphClient
from biomed_ontology.ingest.catalog import catalog_files, load_catalog_normalizer
from biomed_ontology.lake import steps as lake_steps
from biomed_ontology.ontology import rdf as rdf_mod


def test_load_catalog_normalizer_resolves_golden_entity():
    normalizer = load_catalog_normalizer()
    hit = normalizer.concept("HMD:ENT:DC:savolitinib")
    assert hit is not None
    assert isinstance(normalizer, ConceptCatalog)


def test_catalog_files_skips_ambiguity():
    names = [p.name for p in catalog_files()]
    assert "ambiguity.yaml" not in names
    assert names


def test_lake_steps_do_not_import_pipeline():
    text = Path(lake_steps.__file__).read_text(encoding="utf-8")
    assert "build_literature_base" not in text
    assert "biomed_ontology.pipeline" not in text
    assert "IdentityService" in text


def test_rdf_does_not_eagerly_import_graphdb():
    for line in Path(rdf_mod.__file__).read_text(encoding="utf-8").splitlines():
        if line.startswith("from biomed_ontology.foundation.graphdb"):
            raise AssertionError(f"rdf.py 仍急切导入 graphdb：{line}")


def test_graphdb_client_satisfies_graph_client():
    from biomed_ontology.foundation.graphdb import GraphDbClient

    assert isinstance(GraphDbClient(base_url="http://localhost", repository="hmd"), GraphClient)
