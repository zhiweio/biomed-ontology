"""YAML seed → GraphDB + Milvus Evidence + OpenMetadata（工程入库链路）。

Semantic Ops 运行时从三后端读取；YAML 不得作为生产读路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from biomed_ontology.config import Settings, settings
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
    cfg: Settings | None = None,
    graphdb: GraphDbClient | None = None,
    milvus_uri: str | None = None,
    openmetadata_url: str | None = None,
    require_graphdb: bool = True,
    require_milvus: bool = True,
    require_om: bool = True,
) -> SyncResult:
    cfg = cfg or settings
    wm = world or load_world_model()
    details: list[str] = []
    gdb = graphdb or GraphDbClient.from_settings(cfg)
    milvus_uri = milvus_uri or cfg.milvus_uri
    om_url = openmetadata_url or cfg.openmetadata_url

    graphdb_ok = False
    milvus_ok = False
    om_ok = False
    evidence_n = 0
    assets_n = 0

    if gdb.health():
        ensure_repository(gdb)
        gdb.clear_graph(GRAPH_ONTOLOGY)
        gdb.load_turtle(_entities_turtle(wm), graph_uri=GRAPH_ONTOLOGY)
        ttl_know, ttl_prov = _claims_turtle(wm)
        gdb.clear_graph(GRAPH_KNOWLEDGE)
        gdb.load_turtle(ttl_know, graph_uri=GRAPH_KNOWLEDGE)
        gdb.clear_graph(GRAPH_PROVENANCE)
        gdb.load_turtle(ttl_prov, graph_uri=GRAPH_PROVENANCE)
        graphdb_ok = True
        details.append(
            f"graphdb: ontology={len(wm.entities)} entities, "
            f"knowledge+provenance claims={len(wm.claims)}"
        )
    else:
        details.append("graphdb: unreachable — skipped RDF sync")
        if require_graphdb:
            raise RuntimeError("GraphDB 为必选后端，请先 task foundation:up")

    try:
        evidence_n = _upsert_evidence_milvus(wm, milvus_uri)
        milvus_ok = True
        details.append(f"milvus: upserted {evidence_n} evidence rows → foundation_evidence")
    except Exception as exc:
        details.append(f"milvus: FAILED {exc}")
        if require_milvus:
            raise RuntimeError(
                f"Milvus 为必选后端，同步失败：{exc}。请先 task milvus:up / task foundation:up"
            ) from exc

    om = OpenMetadataClient.from_settings(cfg)
    if om_url:
        om.base_url = om_url
    try:
        om.ping()
        assets_n = om.upsert_assets(list(wm.assets))
        om_ok = True
        details.append(f"openmetadata: upserted {assets_n} glossary terms ({om.base_url})")
    except Exception as exc:
        details.append(f"openmetadata: FAILED {exc}")
        if require_om:
            raise RuntimeError(
                f"OpenMetadata 为必选后端，同步失败：{exc}。"
                "请在 Settings / .env 配置 openmetadata_email / openmetadata_password"
            ) from exc

    return SyncResult(
        entities=len(wm.entities),
        claims=len(wm.claims),
        evidence_upserted=evidence_n,
        assets=assets_n or len(wm.assets),
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
        if e.definition:
            lines.append(f'  hmd:definition "{_esc(e.definition)}" ;')
        if e.modality:
            lines.append(f'  hmd:modality "{_esc(e.modality)}" ;')
        if e.status:
            lines.append(f'  hmd:status "{_esc(e.status)}" ;')
        if e.program_id:
            lines.append(f"  hmd:program <{entity_iri(e.program_id)}> ;")
        if e.candidate_id:
            lines.append(f"  hmd:candidate <{entity_iri(e.candidate_id)}> ;")
        if e.asset_fqn:
            lines.append(f'  hmd:assetFqn "{_esc(e.asset_fqn)}" ;')
        if e.pmid:
            lines.append(f'  hmd:pmid "{_esc(e.pmid)}" ;')
        for x in e.exact_match_xrefs:
            lines.append(f'  skos:exactMatch "{_esc(x)}" ;')
        for a in e.aliases:
            lines.append(f'  skos:altLabel "{_esc(a)}" ;')
        for t in e.targets:
            lines.append(f"  hmd:targets <{entity_iri(t)}> ;")
        for ind in e.indications:
            lines.append(f"  hmd:indication <{entity_iri(ind)}> ;")
        lines.append('  rdfs:label "' + _esc(e.preferred_label_en) + '" .')
        lines.append("")
    return "\n".join(lines)


def _claims_turtle(wm: WorldModel) -> tuple[str, str]:
    know = [
        "@prefix hmd: <" + HMD_NS + "> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "",
    ]
    prov_lines = [
        "@prefix hmd: <" + HMD_NS + "> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "",
    ]
    for c in wm.claims:
        subj = f"<{entity_iri(c.subject_id)}>"
        if c.object_id:
            obj = f"<{entity_iri(c.object_id)}>"
            know.append(f"{subj} hmd:{c.predicate} {obj} .")
        claim = f"<{HMD_NS}claim/{_esc_iri(c.claim_id)}>"
        prov_lines.append(f"{claim} a hmd:KnowledgeClaim ;")
        prov_lines.append(f"  hmd:subject {subj} ;")
        prov_lines.append(f'  hmd:predicate "{_esc(c.predicate)}" ;')
        if c.object_id:
            prov_lines.append(f"  hmd:object <{entity_iri(c.object_id)}> ;")
        if c.source_id:
            prov_lines.append(f'  hmd:sourceId "{_esc(c.source_id)}" ;')
            prov_lines.append(f'  prov:wasDerivedFrom "{_esc(c.source_id)}" ;')
        if c.source_type:
            prov_lines.append(f'  hmd:sourceType "{_esc(c.source_type)}" ;')
        if c.span:
            prov_lines.append(f'  hmd:span "{_esc(c.span)}" ;')
        for eid in c.evidence_ids:
            prov_lines.append(f'  hmd:evidenceId "{_esc(eid)}" ;')
        prov_lines.append(f'  hmd:extractedBy "{_esc(c.extracted_by)}" ;')
        prov_lines.append(f"  hmd:confidence {c.confidence} .")
        prov_lines.append("")
    return "\n".join(know), "\n".join(prov_lines)


def _upsert_evidence_milvus(wm: WorldModel, uri: str) -> int:
    """Foundation seed → Milvus。种子体量小，用确定性 dense 占位（非生产语料嵌入）。"""
    rows: list[dict[str, Any]] = []
    for i, e in enumerate(wm.evidence):
        vec = [((i + 1) * (j + 1) % 97) / 97.0 for j in range(32)]
        rows.append(
            {
                "evidence_id": e.evidence_id,
                "text": (e.quote or e.text)[:8000],
                "quote": (e.quote or e.text)[:8000],
                "entity_ids": list(e.entity_ids)[:64],
                "doc_id": (e.doc_id or "")[:256],
                "collection": (e.collection or "literature")[:64],
                "score": float(e.score),
                "dense": vec,
            }
        )
    return _raw_milvus_upsert(uri, rows)


def _raw_milvus_upsert(uri: str, rows: list[dict[str, Any]]) -> int:
    from pymilvus import DataType, MilvusClient

    client = MilvusClient(uri=uri)
    name = "foundation_evidence"
    # schema 演进：缺字段则重建
    need_recreate = True
    if client.has_collection(name):
        try:
            info = client.describe_collection(name)
            fields = {f["name"] for f in info.get("fields", [])}
            required = {
                "evidence_id",
                "text",
                "quote",
                "entity_ids",
                "doc_id",
                "collection",
                "score",
                "dense",
            }
            need_recreate = not required <= fields
        except Exception:
            need_recreate = True
    if need_recreate:
        if client.has_collection(name):
            client.drop_collection(name)
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("evidence_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("text", DataType.VARCHAR, max_length=8192)
        schema.add_field("quote", DataType.VARCHAR, max_length=8192)
        schema.add_field(
            "entity_ids",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=64,
            max_length=128,
        )
        schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
        schema.add_field("collection", DataType.VARCHAR, max_length=64)
        schema.add_field("score", DataType.FLOAT)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=32)
        idx = client.prepare_index_params()
        idx.add_index(field_name="dense", metric_type="IP", index_type="FLAT")
        client.create_collection(name, schema=schema, index_params=idx)

    client.upsert(collection_name=name, data=rows)
    client.flush(name)
    return len(rows)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _esc_iri(s: str) -> str:
    return s.replace(" ", "_").replace(":", "_")
