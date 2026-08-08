"""Foundation Semantic Access Layer。

查询路径强制 GraphDB（关系）+ Milvus（证据）+ OpenMetadata（资产）。
YAML 仅作离线资源，经 `hmd foundation sync` 校验入库后供运行时读取；禁止 YAML fallback。
Entity Resolution 词典仍可从本地 seed 加载（非查询回落）。
"""

from __future__ import annotations

from typing import Any

from biomed_ontology.config import settings
from biomed_ontology.foundation.catalog import OpenMetadataClient
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
)
from biomed_ontology.foundation.models import EvidenceHit
from biomed_ontology.foundation.store import fetch_claims, fetch_entity, fetch_related_ids
from biomed_ontology.foundation.world import WorldModel

__all__ = ["SEMANTIC_OPS", "FoundationApi"]


class BackendUnavailableError(RuntimeError):
    """GraphDB / Milvus / OpenMetadata 不可用；禁止回落 YAML。"""


def _search_evidence_milvus(
    *,
    query: str | None,
    entity_ids: list[str] | None,
) -> list[EvidenceHit]:
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise BackendUnavailableError(
            "Milvus 客户端不可用（缺少 pymilvus）。请 uv sync --extra vector"
        ) from exc

    uri = settings.milvus_uri
    try:
        client = MilvusClient(uri=uri)
        if not client.has_collection("foundation_evidence"):
            raise BackendUnavailableError(
                "Milvus 集合 foundation_evidence 不存在；请先 hmd foundation sync"
            )
        filt: str | None = None
        if entity_ids:
            quoted = ", ".join(f'"{e}"' for e in entity_ids)
            filt = f"ARRAY_CONTAINS_ANY(entity_ids, [{quoted}])"
        fields = ["evidence_id", "text", "quote", "entity_ids", "doc_id", "collection", "score"]
        try:
            rows = client.query(
                collection_name="foundation_evidence",
                filter=filt or 'evidence_id != ""',
                output_fields=fields,
                limit=64,
            )
        except Exception:
            rows = client.query(
                collection_name="foundation_evidence",
                filter='evidence_id != ""',
                output_fields=["evidence_id", "text", "entity_ids", "quote", "doc_id", "collection", "score"],
                limit=256,
            )
            if entity_ids:
                wanted = set(entity_ids)
                rows = [r for r in rows if wanted & set(r.get("entity_ids") or [])]
    except BackendUnavailableError:
        raise
    except Exception as exc:
        raise BackendUnavailableError(f"Milvus Evidence Index 不可用：{exc}") from exc

    hits: list[EvidenceHit] = []
    for r in rows:
        text = str(r.get("quote") or r.get("text") or "")
        if query and query.lower() not in text.lower():
            continue
        hits.append(
            EvidenceHit(
                evidence_id=str(r["evidence_id"]),
                text=str(r.get("text") or text),
                quote=text,
                entity_ids=list(r.get("entity_ids") or []),
                doc_id=r.get("doc_id"),
                collection=str(r.get("collection") or "milvus"),
                score=float(r.get("score") or 1.0),
            )
        )
    return hits


SEMANTIC_OPS: list[dict[str, str]] = [
    {
        "name": "resolve_entity",
        "summary": "文本/别名 → Enterprise Entity ID（词典 / BERN2 候选 + Resolver）",
    },
    {
        "name": "get_entity",
        "summary": "按 Enterprise ID 取实体（GraphDB）",
    },
    {
        "name": "get_relationships",
        "summary": "KnowledgeClaim（GraphDB provenance）",
    },
    {
        "name": "find_related_entities",
        "summary": "一跳相关企业实体（GraphDB）",
    },
    {
        "name": "search_evidence",
        "summary": "Evidence Index（Milvus）",
    },
    {
        "name": "search_assets",
        "summary": "企业资产（OpenMetadata Glossary）",
    },
    {
        "name": "get_entity_evidence",
        "summary": "实体 → 证据（Milvus）",
    },
    {
        "name": "get_entity_assets",
        "summary": "实体 → 资产（OpenMetadata）",
    },
    {
        "name": "get_entity_context",
        "summary": "聚合：GraphDB + Milvus + OpenMetadata（禁止 YAML fallback）",
    },
]


class FoundationApi:
    def __init__(
        self,
        world: WorldModel,
        *,
        graphdb: GraphDbClient | None = None,
        openmetadata: OpenMetadataClient | None = None,
    ) -> None:
        self.world = world
        self.graphdb = graphdb or GraphDbClient.from_settings()
        self.openmetadata = openmetadata or OpenMetadataClient.from_settings()

    def _require_graphdb(self) -> GraphDbClient:
        if not self.graphdb.health():
            raise BackendUnavailableError(
                "GraphDB 不可用。请 task foundation:up 后执行 hmd foundation sync"
            )
        return self.graphdb

    def resolve_entity(self, text: str, *, type_hint: str | None = None) -> dict[str, Any]:
        """ER 使用本地词典/Resolver（seed）；不读 YAML 作为 World Model 查询回落。"""
        assert self.world.resolver is not None
        hits = self.world.resolver.resolve_text(text)
        if type_hint and len(hits) == 1 and hits[0].canonical_entity is None:
            hits = [self.world.resolver.resolve_mention(text, type_hint=type_hint)]
        return {
            "ontology_release_id": self.world.release_id,
            "query": text,
            "resolved": [h.to_dict() for h in hits],
        }

    def get_entity(self, enterprise_id: str) -> dict[str, Any]:
        gdb = self._require_graphdb()
        try:
            ent = fetch_entity(gdb, enterprise_id)
        except Exception as exc:
            raise BackendUnavailableError(f"GraphDB 读实体失败：{exc}") from exc
        if ent is None:
            return {
                "ontology_release_id": self.world.release_id,
                "enterprise_id": enterprise_id,
                "found": False,
                "backend": "graphdb",
            }
        return {
            "ontology_release_id": self.world.release_id,
            "found": True,
            "entity": ent.to_dict(),
            "named_graphs": {
                "ontology": GRAPH_ONTOLOGY,
                "knowledge": GRAPH_KNOWLEDGE,
                "provenance": GRAPH_PROVENANCE,
            },
            "backend": "graphdb",
        }

    def get_relationships(
        self, enterprise_id: str, *, predicate: str | None = None
    ) -> dict[str, Any]:
        gdb = self._require_graphdb()
        try:
            claims = fetch_claims(gdb, enterprise_id, predicate=predicate)
        except Exception as exc:
            raise BackendUnavailableError(f"GraphDB 读关系失败：{exc}") from exc
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "claims": [c.to_dict() for c in claims],
            "backend": "graphdb",
        }

    def find_related_entities(self, enterprise_id: str) -> dict[str, Any]:
        gdb = self._require_graphdb()
        try:
            ids = fetch_related_ids(gdb, enterprise_id)
            related = []
            for eid in ids:
                ent = fetch_entity(gdb, eid)
                if ent:
                    related.append(ent.to_dict())
        except Exception as exc:
            raise BackendUnavailableError(f"GraphDB 读相关实体失败：{exc}") from exc
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "related": related,
            "backend": "graphdb",
        }

    def search_evidence(
        self,
        *,
        query: str | None = None,
        entity_ids: list[str] | None = None,
        require_quote: bool = True,
    ) -> dict[str, Any]:
        hits = _search_evidence_milvus(query=query, entity_ids=entity_ids)
        if require_quote:
            hits = [e for e in hits if (e.quote or e.text)]
        hits.sort(key=lambda e: (0 if e.quote else 1, -e.score))
        return {
            "ontology_release_id": self.world.release_id,
            "query": query,
            "entity_ids": entity_ids or [],
            "evidence": [e.to_dict() for e in hits],
            "policy": "evidence_first",
            "backend": "milvus",
        }

    def search_assets(
        self, *, query: str | None = None, entity_ids: list[str] | None = None
    ) -> dict[str, Any]:
        try:
            self.openmetadata.ping()
            hits = self.openmetadata.search_assets(query=query, entity_ids=entity_ids)
        except Exception as exc:
            raise BackendUnavailableError(f"OpenMetadata 不可用：{exc}") from exc
        return {
            "ontology_release_id": self.world.release_id,
            "query": query,
            "entity_ids": entity_ids or [],
            "assets": [a.to_dict() for a in hits],
            "backend": "openmetadata",
        }

    def get_entity_evidence(self, enterprise_id: str) -> dict[str, Any]:
        out = self.search_evidence(entity_ids=[enterprise_id], require_quote=True)
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "evidence": out["evidence"],
            "backend": out["backend"],
        }

    def get_entity_assets(self, enterprise_id: str) -> dict[str, Any]:
        out = self.search_assets(entity_ids=[enterprise_id])
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "assets": out["assets"],
            "backend": out["backend"],
        }

    def get_entity_context(self, enterprise_id: str) -> dict[str, Any]:
        """强制三后端聚合；无 YAML fallback。"""
        ent = self.get_entity(enterprise_id)
        if not ent.get("found"):
            return ent

        rel = self.get_relationships(enterprise_id)
        relationships = rel["claims"]
        related_resp = self.find_related_entities(enterprise_id)
        related = related_resp["related"]
        related_by_id = {e["enterprise_id"]: e for e in related}
        entity = ent["entity"]

        target_ids = list(entity.get("targets") or [])
        for c in relationships:
            oid = c.get("object_id")
            if (
                c.get("predicate") == "targets"
                and oid
                and oid not in target_ids
                and c.get("subject_id") == enterprise_id
            ):
                target_ids.append(oid)

        disease_ids = list(entity.get("indications") or [])
        for c in relationships:
            oid = c.get("object_id")
            if (
                c.get("predicate") == "investigates"
                and oid
                and oid not in disease_ids
                and c.get("subject_id") == enterprise_id
            ):
                disease_ids.append(oid)

        targets = [_entity_ref(related_by_id, tid, self) for tid in target_ids]
        diseases = [_entity_ref(related_by_id, did, self) for did in disease_ids]

        ev_out = self.get_entity_evidence(enterprise_id)
        evidence_hits = {
            e["evidence_id"]: EvidenceHit(
                evidence_id=e["evidence_id"],
                text=e.get("text") or "",
                quote=e.get("quote"),
                entity_ids=list(e.get("entity_ids") or []),
                doc_id=e.get("doc_id"),
                collection=e.get("collection") or "",
                score=float(e.get("score") or 0),
            )
            for e in ev_out["evidence"]
        }
        citation_evidence = _build_citation_evidence(
            relationships,
            evidence_hits,
            enterprise_id=enterprise_id,
        )
        claimed_eids = {row["id"] for row in citation_evidence if row.get("id")}
        for hit in evidence_hits.values():
            if hit.evidence_id in claimed_eids:
                continue
            citation_evidence.append(
                {
                    "id": hit.evidence_id,
                    "type": _evidence_type(hit),
                    "claim": None,
                    "span": hit.quote or hit.text,
                    "doc_id": hit.doc_id,
                    "score": hit.score,
                    "entity_ids": list(hit.entity_ids),
                }
            )

        assets_out = self.get_entity_assets(enterprise_id)
        assets = assets_out["assets"]
        internal_assets = [
            {
                "id": a.get("asset_fqn"),
                "type": a.get("asset_type"),
                "name": a.get("name"),
                "entity_ids": a.get("entity_ids") or [],
                "url": a.get("url"),
                "description": a.get("description"),
            }
            for a in assets
        ]

        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "entity": entity,
            "targets": targets,
            "diseases": diseases,
            "evidence": citation_evidence,
            "internal_assets": internal_assets,
            "relationships": relationships,
            "related_entities": related,
            "assets": assets,
            "backends": {
                "entity": "graphdb",
                "relationships": "graphdb",
                "related": "graphdb",
                "evidence": "milvus",
                "assets": "openmetadata",
            },
        }

    def dispatch(self, op: str, **kwargs: Any) -> dict[str, Any]:
        fn = getattr(self, op, None)
        if fn is None or op.startswith("_"):
            raise KeyError(f"未知语义操作：{op}")
        return fn(**kwargs)

    def golden_path(self, candidate_key: str = "savolitinib") -> dict[str, Any]:
        resolve = self.resolve_entity(candidate_key)
        canonical = next(
            (r["canonical_entity"] for r in resolve["resolved"] if r.get("canonical_entity")),
            None,
        )
        if not canonical:
            return {"ok": False, "reason": "candidate_unresolved", "resolve": resolve}
        ctx = self.get_entity_context(canonical)
        return {
            "ok": True,
            "path": "DrugCandidate→Target→Disease→Evidence→Asset",
            "canonical_entity": canonical,
            "query": candidate_key,
            "resolve": resolve,
            "context": ctx,
            "backends": ctx.get("backends"),
        }


def _entity_ref(
    related_by_id: dict[str, dict[str, Any]],
    enterprise_id: str,
    api: FoundationApi,
) -> dict[str, Any]:
    row = related_by_id.get(enterprise_id)
    if row is None:
        got = api.get_entity(enterprise_id)
        row = got.get("entity") if got.get("found") else {"enterprise_id": enterprise_id}
    assert row is not None
    return {
        "id": row.get("enterprise_id", enterprise_id),
        "type": row.get("entity_kind"),
        "label": row.get("preferred_label_en"),
        "external_ids": list(row.get("exact_match_xrefs") or []),
    }


def _build_citation_evidence(
    relationships: list[dict[str, Any]],
    evidence_by_id: dict[str, Any],
    *,
    enterprise_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for claim in relationships:
        if claim.get("subject_id") != enterprise_id and claim.get("object_id") != enterprise_id:
            continue
        eids = list(claim.get("evidence_ids") or [])
        if not eids and claim.get("source_id"):
            eids = [claim["source_id"]]
        claim_label = (
            f"{claim.get('subject_id')} {claim.get('predicate')} {claim.get('object_id') or ''}"
        ).strip()
        span = claim.get("span")
        if not eids:
            if span:
                key = (claim.get("claim_id") or claim_label, span)
                if key not in seen:
                    seen.add(key)
                    out.append(
                        {
                            "id": claim.get("source_id"),
                            "type": claim.get("source_type"),
                            "claim": claim_label,
                            "span": span,
                            "confidence": claim.get("confidence"),
                            "predicate": claim.get("predicate"),
                        }
                    )
            continue
        for eid in eids:
            hit = evidence_by_id.get(eid)
            quote = span or (hit.quote if hit else None) or (hit.text if hit else None)
            key = (eid, claim.get("claim_id") or claim_label)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": eid,
                    "type": _evidence_type(hit) if hit else claim.get("source_type"),
                    "claim": claim_label,
                    "span": quote,
                    "confidence": claim.get("confidence"),
                    "predicate": claim.get("predicate"),
                    "doc_id": hit.doc_id if hit else claim.get("source_id"),
                    "score": hit.score if hit else None,
                    "entity_ids": list(hit.entity_ids) if hit else [],
                }
            )
    return out


def _evidence_type(hit: Any) -> str:
    collection = (getattr(hit, "collection", None) or "").lower()
    doc_id = (getattr(hit, "doc_id", None) or "").lower()
    if collection == "patents" or doc_id.startswith("patent:"):
        return "Patent"
    if collection == "lims" or doc_id.startswith("lims:"):
        return "LIMS"
    if collection in {"internal_docs", "eln"} or doc_id.startswith("eln:"):
        return "ELN"
    if getattr(hit, "pmid", None) or doc_id.startswith("pubmed:") or collection == "literature":
        return "PubMed"
    if collection == "milvus":
        return "Evidence"
    return collection or "Evidence"
