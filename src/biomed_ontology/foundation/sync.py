"""把 foundation YAML seed 同步到 GraphDB + Milvus Evidence + OpenMetadata。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from biomed_ontology.foundation.catalog import OpenMetadataClient
from biomed_ontology.foundation.graphdb import GraphDbClient, ensure_repository
from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
    HMD_NS,
)
from biomed_ontology.foundation.world import WorldModel, entity_iri, load_world_model

__all__ = ["SyncResult", "sync_world_model"]


@dataclass
class SyncResult:
    entities: int
    claims: int
    evidence_upserted: int
    assets: int
    graphdb_ok: bool
    milvus_ok: bool
    om_ok: bool
    details: list[str]


def sync_world_model(
    world: WorldModel | None = None,
    *,
    graphdb: GraphDbClient | None = None,
    milvus_uri: str = "http://localhost:19530",
    openmetadata_url: str | None = None,
    require_milvus: bool = True,
) -> SyncResult:
    wm = world or load_world_model()
    details: list[str] = []
    gdb = graphdb or GraphDbClient()
    graphdb_ok = False
    milvus_ok = False
    om_ok = False
    evidence_n = 0

    if gdb.health():
        ensure_repository(gdb)
        ttl_ont = _entities_turtle(wm)
        gdb.clear_graph(GRAPH_ONTOLOGY)
        gdb.load_turtle(ttl_ont, graph_uri=GRAPH_ONTOLOGY)
        ttl_know, ttl_prov = _claims_turtle(wm)
        gdb.clear_graph(GRAPH_KNOWLEDGE)
        gdb.load_turtle(ttl_know, graph_uri=GRAPH_KNOWLEDGE)
        gdb.clear_graph(GRAPH_PROVENANCE)
        gdb.load_turtle(ttl_prov, graph_uri=GRAPH_PROVENANCE)
        graphdb_ok = True
        details.append("graphdb: ontology/knowledge/provenance synced")
    else:
        details.append("graphdb: unreachable — skipped RDF sync")

    try:
        evidence_n = _upsert_evidence_milvus(wm, milvus_uri)
        milvus_ok = True
        details.append(f"milvus: upserted {evidence_n} evidence rows")
    except Exception as exc:
        details.append(f"milvus: FAILED {exc}")
        if require_milvus:
            raise RuntimeError(
                f"Milvus 为必选后端，同步失败：{exc}。请先 task milvus:up / task foundation:up"
            ) from exc

    if openmetadata_url:
        import os

        om = OpenMetadataClient(
            base_url=openmetadata_url,
            token=os.environ.get("HMD_OPENMETADATA_TOKEN") or None,
        )
        try:
            ver = om.ping()
            om_ok = True
            detail = f"openmetadata: reachable version={ver.get('version', ver)}"
            if om.token:
                hits = om.search_assets(query="*")
                detail += f" search_hits={len(hits)}"
            else:
                detail += " (no token; search skipped)"
            details.append(detail)
        except Exception as exc:
            details.append(f"openmetadata: {exc}")

    return SyncResult(
        entities=len(wm.entities),
        claims=len(wm.claims),
        evidence_upserted=evidence_n,
        assets=len(wm.assets),
        graphdb_ok=graphdb_ok,
        milvus_ok=milvus_ok,
        om_ok=om_ok,
        details=details,
    )


def _entities_turtle(wm: WorldModel) -> str:
    lines = [
        "@prefix hmd: <" + HMD_NS + "> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]
    for e in wm.entities.values():
        s = f"<{entity_iri(e.enterprise_id)}>"
        lines.append(f"{s} a hmd:{e.entity_kind} ;")
        lines.append(f'  skos:prefLabel "{_esc(e.preferred_label_en)}"@en ;')
        if e.preferred_label_zh:
            lines.append(f'  skos:prefLabel "{_esc(e.preferred_label_zh)}"@zh ;')
        lines.append(f'  hmd:enterpriseId "{_esc(e.enterprise_id)}" ;')
        for x in e.exact_match_xrefs:
            lines.append(f'  skos:exactMatch "{_esc(x)}" ;')
        lines.append('  rdfs:label "' + _esc(e.preferred_label_en) + '" .')
        lines.append("")
    return "\n".join(lines)


def _claims_turtle(wm: WorldModel) -> tuple[str, str]:
    know = [
        "@prefix hmd: <" + HMD_NS + "> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "",
    ]
    prov_lines = list(know)
    for c in wm.claims:
        subj = f"<{entity_iri(c.subject_id)}>"
        if c.object_id:
            obj = f"<{entity_iri(c.object_id)}>"
            know.append(f"{subj} hmd:{c.predicate} {obj} .")
        claim = f"<{HMD_NS}claim/{c.claim_id}>"
        prov_lines.append(f"{claim} a hmd:KnowledgeClaim ;")
        prov_lines.append(f"  hmd:subject {subj} ;")
        prov_lines.append(f'  hmd:predicate "{_esc(c.predicate)}" ;')
        if c.source_id:
            prov_lines.append(f'  prov:wasDerivedFrom "{_esc(c.source_id)}" ;')
        prov_lines.append(f'  hmd:extractedBy "{_esc(c.extracted_by)}" ;')
        prov_lines.append(f"  hmd:confidence {c.confidence} .")
        prov_lines.append("")
    return "\n".join(know), "\n".join(prov_lines)


def _upsert_evidence_milvus(wm: WorldModel, uri: str) -> int:
    """写入 foundation_evidence（独立精简 schema，不用 MilvusBackend/chunks 五列）。

    dense 用确定性假向量仅当 HMD_ALLOW_FAKE_EVIDENCE=1。
    """
    import os

    allow_fake = os.environ.get("HMD_ALLOW_FAKE_EVIDENCE", "1") == "1"
    if not allow_fake:
        raise RuntimeError("生产证据索引需真实 embedder；联调请设 HMD_ALLOW_FAKE_EVIDENCE=1")

    rows: list[dict[str, Any]] = []
    for i, e in enumerate(wm.evidence):
        vec = [((i + 1) * (j + 1) % 97) / 97.0 for j in range(32)]
        rows.append(
            {
                "chunk_id": e.evidence_id,
                "text": e.quote or e.text,
                "entity_ids": list(e.entity_ids),
                "dense_generic": vec,
            }
        )
    return _raw_milvus_upsert(uri, rows)


def _raw_milvus_upsert(uri: str, rows: list[dict[str, Any]]) -> int:
    from pymilvus import DataType, MilvusClient

    client = MilvusClient(uri=uri)
    name = "foundation_evidence"
    if not client.has_collection(name):
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("evidence_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("text", DataType.VARCHAR, max_length=8192)
        schema.add_field(
            "entity_ids",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=64,
            max_length=128,
        )
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=32)
        idx = client.prepare_index_params()
        idx.add_index(field_name="dense", metric_type="IP", index_type="FLAT")
        client.create_collection(name, schema=schema, index_params=idx)
    data = [
        {
            "evidence_id": r["chunk_id"],
            "text": r["text"][:8000],
            "entity_ids": r["entity_ids"][:64],
            "dense": r["dense_generic"],
        }
        for r in rows
    ]
    client.upsert(collection_name=name, data=data)
    client.flush(name)
    return len(data)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
