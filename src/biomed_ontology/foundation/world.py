"""World Model 装配：Enterprise Ontology + Claims + Evidence + Assets。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology.foundation.bern2 import (
    Bern2Client,
    EnterpriseDictionary,
    load_enterprise_dictionary,
)
from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
    HMD_NS,
)
from biomed_ontology.foundation.ids import EnterpriseKind, mint_enterprise_id
from biomed_ontology.foundation.models import (
    AssetHit,
    EnterpriseEntity,
    EvidenceHit,
    KnowledgeClaim,
)
from biomed_ontology.foundation.resolve import EntityResolver, ResolutionIndex

__all__ = ["WorldModel", "load_world_model"]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FOUNDATION = REPO_ROOT / "data" / "foundation"


@dataclass
class WorldModel:
    release_id: str
    entities: dict[str, EnterpriseEntity] = field(default_factory=dict)
    claims: list[KnowledgeClaim] = field(default_factory=list)
    evidence: list[EvidenceHit] = field(default_factory=list)
    assets: list[AssetHit] = field(default_factory=list)
    resolver: EntityResolver | None = None
    named_graphs: dict[str, str] = field(
        default_factory=lambda: {
            "ontology": GRAPH_ONTOLOGY,
            "knowledge": GRAPH_KNOWLEDGE,
            "provenance": GRAPH_PROVENANCE,
        }
    )

    def entity(self, enterprise_id: str) -> EnterpriseEntity | None:
        return self.entities.get(enterprise_id)

    def relationships(
        self,
        enterprise_id: str,
        *,
        predicate: str | None = None,
    ) -> list[KnowledgeClaim]:
        out = [
            c for c in self.claims if c.subject_id == enterprise_id or c.object_id == enterprise_id
        ]
        if predicate:
            out = [c for c in out if c.predicate == predicate]
        return out

    def evidence_for(self, enterprise_id: str) -> list[EvidenceHit]:
        claim_eids: set[str] = set()
        for c in self.claims:
            if c.subject_id == enterprise_id or c.object_id == enterprise_id:
                claim_eids.update(c.evidence_ids)
        hits = [
            e for e in self.evidence if enterprise_id in e.entity_ids or e.evidence_id in claim_eids
        ]
        # 稳定排序：有 quote 的优先（证据优先）
        hits.sort(key=lambda e: (0 if e.quote else 1, -e.score, e.evidence_id))
        return hits

    def assets_for(self, enterprise_id: str) -> list[AssetHit]:
        return [a for a in self.assets if enterprise_id in a.entity_ids]

    def related_entities(self, enterprise_id: str) -> list[EnterpriseEntity]:
        ids: set[str] = set()
        for c in self.relationships(enterprise_id):
            if c.subject_id != enterprise_id:
                ids.add(c.subject_id)
            if c.object_id and c.object_id != enterprise_id:
                ids.add(c.object_id)
        ent = self.entity(enterprise_id)
        if ent:
            ids.update(ent.targets)
            ids.update(ent.indications)
            if ent.program_id:
                ids.add(ent.program_id)
        return [self.entities[i] for i in sorted(ids) if i in self.entities]


def load_world_model(
    root: Path | None = None,
    *,
    bern2_url: str | None = None,
) -> WorldModel:
    base = root or DEFAULT_FOUNDATION
    entities_path = base / "enterprise_entities.yaml"
    dict_path = base / "enterprise_dictionary.yaml"
    claims_path = base / "knowledge_claims.yaml"
    evidence_path = base / "evidence_index.yaml"
    assets_path = base / "assets.yaml"

    raw = yaml.safe_load(entities_path.read_text(encoding="utf-8")) or {}
    release_id = str(raw.get("release_id", "0.1.0-foundation"))
    entities = [_parse_entity(row) for row in raw.get("entities", [])]
    by_id = {e.enterprise_id: e for e in entities}

    claims = [_parse_claim(r) for r in _load_list(claims_path, "claims")]
    evidence = [_parse_evidence(r) for r in _load_list(evidence_path, "evidence")]
    assets = [_parse_asset(r) for r in _load_list(assets_path, "assets")]

    dictionary = (
        load_enterprise_dictionary(dict_path) if dict_path.exists() else EnterpriseDictionary()
    )
    # 从实体别名回填词典，保证金标路径不依赖第二份手工表遗漏
    for e in entities:
        for alias in {e.preferred_label_en, e.preferred_label_zh, *e.aliases}:
            if not alias:
                continue
            dictionary.entries.append(
                {
                    "mention": alias,
                    "type": e.entity_kind,
                    "enterprise_id": e.enterprise_id,
                    "external_ids": list(e.exact_match_xrefs),
                    "aliases": [alias],
                }
            )
    dictionary.__post_init__()

    bern2 = Bern2Client(base_url=bern2_url, dictionary=dictionary)
    resolver = EntityResolver(ResolutionIndex.from_entities(entities), bern2=bern2)

    return WorldModel(
        release_id=release_id,
        entities=by_id,
        claims=claims,
        evidence=evidence,
        assets=assets,
        resolver=resolver,
    )


def _load_list(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get(key, []))


def _parse_entity(row: dict[str, Any]) -> EnterpriseEntity:
    kind = EnterpriseKind(row["entity_kind"])
    eid = row.get("enterprise_id") or str(mint_enterprise_id(kind, row["key"]))
    return EnterpriseEntity(
        enterprise_id=eid,
        entity_kind=kind.value,
        preferred_label_en=row["preferred_label_en"],
        preferred_label_zh=row.get("preferred_label_zh"),
        definition=row.get("definition"),
        exact_match_xrefs=list(row.get("exact_match_xrefs", [])),
        related_xrefs=list(row.get("related_xrefs", [])),
        aliases=list(row.get("aliases", [])),
        status=row.get("status", "active"),
        targets=[_maybe_ent(t) for t in row.get("targets", [])],
        indications=[_maybe_ent(i) for i in row.get("indications", [])],
        program_id=_maybe_ent(row["program_id"]) if row.get("program_id") else None,
        modality=row.get("modality"),
        therapeutic_area=row.get("therapeutic_area"),
        candidate_id=_maybe_ent(row["candidate_id"]) if row.get("candidate_id") else None,
        target_ids=[_maybe_ent(t) for t in row.get("target_ids", [])],
        indication_ids=[_maybe_ent(i) for i in row.get("indication_ids", [])],
        asset_fqn=row.get("asset_fqn"),
        performed_on=row.get("performed_on"),
        pmid=row.get("pmid"),
        doi=row.get("doi"),
        mentions=[_maybe_ent(m) for m in row.get("mentions", [])],
    )


def _maybe_ent(value: str) -> str:
    if value.startswith("HMD:ENT:"):
        return value
    # 允许 YAML 用短 key，装配阶段由调用方在 golden 数据里写全 ID
    return value


def _parse_claim(row: dict[str, Any]) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=row["claim_id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        object_id=row.get("object_id"),
        object_value=row.get("object_value"),
        confidence=float(row.get("confidence", 1.0)),
        source_id=row.get("source_id"),
        source_type=row.get("source_type", "manual"),
        extracted_by=row.get("extracted_by", "seed"),
        evidence_ids=list(row.get("evidence_ids", [])),
        span=row.get("span"),
        created_at=row.get("created_at"),
    )


def _parse_evidence(row: dict[str, Any]) -> EvidenceHit:
    return EvidenceHit(
        evidence_id=row["evidence_id"],
        text=row["text"],
        entity_ids=list(row.get("entity_ids", [])),
        doc_id=row.get("doc_id"),
        chunk_id=row.get("chunk_id"),
        page=row.get("page"),
        quote=row.get("quote") or row.get("text"),
        collection=row.get("collection", "literature"),
        score=float(row.get("score", 1.0)),
        pmid=row.get("pmid"),
    )


def _parse_asset(row: dict[str, Any]) -> AssetHit:
    return AssetHit(
        asset_fqn=row["asset_fqn"],
        name=row["name"],
        entity_ids=list(row.get("entity_ids", [])),
        description=row.get("description"),
        asset_type=row.get("asset_type", "dataset"),
        url=row.get("url"),
    )


def entity_iri(enterprise_id: str) -> str:
    return HMD_NS + "entity/" + enterprise_id.replace(":", "_")
