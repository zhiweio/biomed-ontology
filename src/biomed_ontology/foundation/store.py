"""GraphDB 运行时读取：Enterprise Entity + KnowledgeClaim。

YAML 只经 sync 入库；Semantic Ops 默认从本模块读关系/实体。
"""

from __future__ import annotations

from typing import Any

from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
    HMD_NS,
)
from biomed_ontology.foundation.models import EnterpriseEntity, KnowledgeClaim
from biomed_ontology.foundation.world import entity_iri

__all__ = [
    "enterprise_id_from_iri",
    "fetch_claims",
    "fetch_entity",
    "fetch_related_ids",
]


def enterprise_id_from_iri(iri: str) -> str | None:
    """https://.../entity/HMD_ENT_DC_savolitinib → HMD:ENT:DC:savolitinib"""
    marker = "/entity/"
    if marker not in iri:
        return None
    local = iri.rsplit(marker, 1)[-1]
    if local.startswith("HMD_ENT_"):
        rest = local[len("HMD_ENT_") :]
        parts = rest.split("_", 1)
        if len(parts) == 2:
            return f"HMD:ENT:{parts[0]}:{parts[1]}"
    return None


def fetch_entity(client: GraphDbClient, enterprise_id: str) -> EnterpriseEntity | None:
    iri = entity_iri(enterprise_id)
    q = f"""
    PREFIX hmd: <{HMD_NS}>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?p ?o WHERE {{
      GRAPH <{GRAPH_ONTOLOGY}> {{
        <{iri}> ?p ?o .
      }}
    }}
    """
    rows = client.query(q)
    if not rows:
        return None

    kind = "EnterpriseEntity"
    label_en: str | None = None
    label_zh: str | None = None
    definition: str | None = None
    xrefs: list[str] = []
    aliases: list[str] = []
    targets: list[str] = []
    indications: list[str] = []
    program_id: str | None = None
    modality: str | None = None
    status = "active"
    candidate_id: str | None = None
    asset_fqn: str | None = None
    pmid: str | None = None

    for row in rows:
        p, o = row.get("p", ""), row.get("o", "")
        if p.endswith("#type") or p.endswith("rdf-syntax-ns#type"):
            if o.startswith(HMD_NS):
                kind = o[len(HMD_NS) :]
        elif p.endswith("prefLabel") or p.endswith("rdfs#label"):
            # lang tags stripped by GraphDB binding value
            if label_en is None:
                label_en = o
            elif o != label_en and label_zh is None:
                label_zh = o
        elif p.endswith("definition"):
            definition = o
        elif p.endswith("exactMatch"):
            xrefs.append(o)
        elif p.endswith("altLabel"):
            aliases.append(o)
        elif p.endswith("targets") or p.endswith("/targets"):
            eid = enterprise_id_from_iri(o)
            if eid:
                targets.append(eid)
        elif p.endswith("indication") or p.endswith("indications"):
            eid = enterprise_id_from_iri(o)
            if eid:
                indications.append(eid)
        elif p.endswith("program") or p.endswith("programId"):
            program_id = enterprise_id_from_iri(o) or o
        elif p.endswith("modality"):
            modality = o
        elif p.endswith("status"):
            status = o
        elif p.endswith("candidate") or p.endswith("candidateId"):
            candidate_id = enterprise_id_from_iri(o) or o
        elif p.endswith("assetFqn"):
            asset_fqn = o
        elif p.endswith("pmid"):
            pmid = o
        elif p.endswith("enterpriseId"):
            enterprise_id = o

    if not label_en:
        label_en = enterprise_id
    return EnterpriseEntity(
        enterprise_id=enterprise_id,
        entity_kind=kind,
        preferred_label_en=label_en,
        preferred_label_zh=label_zh,
        definition=definition,
        exact_match_xrefs=xrefs,
        aliases=aliases,
        status=status,
        targets=targets,
        indications=indications,
        program_id=program_id,
        modality=modality,
        candidate_id=candidate_id,
        asset_fqn=asset_fqn,
        pmid=pmid,
    )


def fetch_claims(
    client: GraphDbClient,
    enterprise_id: str,
    *,
    predicate: str | None = None,
) -> list[KnowledgeClaim]:
    iri = entity_iri(enterprise_id)
    pred_filter = f'FILTER(?pred = "{predicate}")' if predicate else ""
    q = f"""
    PREFIX hmd: <{HMD_NS}>
    PREFIX prov: <http://www.w3.org/ns/prov#>
    SELECT ?claim ?pred ?subj ?obj ?conf ?source ?stype ?span ?extracted ?evid
    WHERE {{
      GRAPH <{GRAPH_PROVENANCE}> {{
        ?claim a hmd:KnowledgeClaim ;
               hmd:subject ?subj ;
               hmd:predicate ?pred .
        OPTIONAL {{ ?claim hmd:object ?obj }}
        OPTIONAL {{ ?claim hmd:confidence ?conf }}
        OPTIONAL {{ ?claim hmd:sourceId ?source }}
        OPTIONAL {{ ?claim prov:wasDerivedFrom ?source }}
        OPTIONAL {{ ?claim hmd:sourceType ?stype }}
        OPTIONAL {{ ?claim hmd:span ?span }}
        OPTIONAL {{ ?claim hmd:extractedBy ?extracted }}
        OPTIONAL {{ ?claim hmd:evidenceId ?evid }}
      }}
      FILTER(?subj = <{iri}> || ?obj = <{iri}>)
      {pred_filter}
    }}
    """
    rows = client.query(q)
    by_id: dict[str, KnowledgeClaim] = {}
    for row in rows:
        claim_iri = row.get("claim") or ""
        cid = claim_iri.rsplit("/", 1)[-1] if claim_iri else row.get("pred", "claim")
        subj = enterprise_id_from_iri(row.get("subj", "")) or ""
        obj = enterprise_id_from_iri(row.get("obj", "")) if row.get("obj") else None
        if cid not in by_id:
            conf_raw = row.get("conf") or "1.0"
            try:
                conf = float(conf_raw)
            except ValueError:
                conf = 1.0
            by_id[cid] = KnowledgeClaim(
                claim_id=cid,
                subject_id=subj,
                predicate=row.get("pred") or "",
                object_id=obj,
                confidence=conf,
                source_id=row.get("source") or None,
                source_type=row.get("stype") or "manual",
                extracted_by=row.get("extracted") or "graphdb",
                evidence_ids=[],
                span=row.get("span") or None,
            )
        evid = row.get("evid")
        if evid and evid not in by_id[cid].evidence_ids:
            by_id[cid].evidence_ids.append(evid)

    # 若 provenance 空，回落 knowledge 图三元组
    if not by_id:
        q2 = f"""
        PREFIX hmd: <{HMD_NS}>
        SELECT ?s ?p ?o WHERE {{
          GRAPH <{GRAPH_KNOWLEDGE}> {{
            {{ <{iri}> ?p ?o . BIND(<{iri}> AS ?s) }}
            UNION
            {{ ?s ?p <{iri}> . BIND(<{iri}> AS ?o) }}
          }}
          FILTER(STRSTARTS(STR(?p), "{HMD_NS}"))
        }}
        """
        for i, row in enumerate(client.query(q2)):
            pred = row.get("p", "").rsplit("/", 1)[-1]
            if predicate and pred != predicate:
                continue
            subj = enterprise_id_from_iri(row.get("s", "")) or ""
            obj = enterprise_id_from_iri(row.get("o", ""))
            cid = f"kg:{pred}:{i}"
            by_id[cid] = KnowledgeClaim(
                claim_id=cid,
                subject_id=subj,
                predicate=pred,
                object_id=obj,
                confidence=1.0,
                source_type="graphdb",
                extracted_by="graphdb",
            )
    return list(by_id.values())


def fetch_related_ids(client: GraphDbClient, enterprise_id: str) -> list[str]:
    ids: set[str] = set()
    for c in fetch_claims(client, enterprise_id):
        if c.subject_id and c.subject_id != enterprise_id:
            ids.add(c.subject_id)
        if c.object_id and c.object_id != enterprise_id:
            ids.add(c.object_id)
    ent = fetch_entity(client, enterprise_id)
    if ent:
        ids.update(ent.targets)
        ids.update(ent.indications)
        if ent.program_id:
            ids.add(ent.program_id)
    return sorted(ids)


def entity_to_dict_from_graph(client: GraphDbClient, enterprise_id: str) -> dict[str, Any] | None:
    ent = fetch_entity(client, enterprise_id)
    return ent.to_dict() if ent else None
