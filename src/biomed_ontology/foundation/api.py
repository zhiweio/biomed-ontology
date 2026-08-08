"""Foundation Semantic Access Layer。

对 Agent 暴露语义操作，隐藏 GraphDB / Milvus / OpenMetadata / BERN2。
"""

from __future__ import annotations

import os
from typing import Any

from biomed_ontology.foundation.models import AssetHit, EvidenceHit
from biomed_ontology.foundation.world import WorldModel

__all__ = ["SEMANTIC_OPS", "FoundationApi"]


def _search_evidence_milvus(
    *,
    query: str | None,
    entity_ids: list[str] | None,
) -> list[EvidenceHit] | None:
    """读 foundation_evidence；不可用时返回 None（回落 YAML seed）。"""
    if os.environ.get("HMD_EVIDENCE_BACKEND", "auto") == "yaml":
        return None
    try:
        from pymilvus import MilvusClient
    except ImportError:
        return None

    uri = os.environ.get("HMD_MILVUS_URI", "http://localhost:19530")
    try:
        client = MilvusClient(uri=uri)
        if not client.has_collection("foundation_evidence"):
            return None
        filt: str | None = None
        if entity_ids:
            # ARRAY_CONTAINS_ANY 在部分版本可用；失败则全量后本地过滤
            quoted = ", ".join(f'"{e}"' for e in entity_ids)
            filt = f"ARRAY_CONTAINS_ANY(entity_ids, [{quoted}])"
        rows: list[dict[str, Any]]
        try:
            rows = client.query(
                collection_name="foundation_evidence",
                filter=filt or 'evidence_id != ""',
                output_fields=["evidence_id", "text", "entity_ids"],
                limit=64,
            )
        except Exception:
            rows = client.query(
                collection_name="foundation_evidence",
                filter='evidence_id != ""',
                output_fields=["evidence_id", "text", "entity_ids"],
                limit=256,
            )
            if entity_ids:
                wanted = set(entity_ids)
                rows = [r for r in rows if wanted & set(r.get("entity_ids") or [])]
    except Exception:
        return None

    hits: list[EvidenceHit] = []
    for r in rows:
        text = str(r.get("text") or "")
        if query and query.lower() not in text.lower():
            continue
        hits.append(
            EvidenceHit(
                evidence_id=str(r["evidence_id"]),
                text=text,
                quote=text,
                entity_ids=list(r.get("entity_ids") or []),
                score=1.0,
                collection="milvus",
            )
        )
    return hits


SEMANTIC_OPS: list[dict[str, str]] = [
    {
        "name": "resolve_entity",
        "summary": "文本/别名 → Enterprise Entity ID（经 BERN2 候选 + Entity Resolution）",
    },
    {
        "name": "get_entity",
        "summary": "按 Enterprise ID 取实体详情与外部映射",
    },
    {
        "name": "get_relationships",
        "summary": "实体相关 KnowledgeClaim（含 provenance 字段）",
    },
    {
        "name": "find_related_entities",
        "summary": "一跳相关企业实体",
    },
    {
        "name": "search_evidence",
        "summary": "Evidence Index：可引用原文片段（证据优先）",
    },
    {
        "name": "search_assets",
        "summary": "OpenMetadata 企业数据资产上下文",
    },
    {
        "name": "get_entity_evidence",
        "summary": "实体 → 证据（经 claim.evidence_ids 与 entity_ids）",
    },
    {
        "name": "get_entity_assets",
        "summary": "实体 → 企业资产",
    },
    {
        "name": "get_entity_context",
        "summary": "聚合：entity + targets + diseases + evidence(claim/span) + internal_assets",
    },
]


class FoundationApi:
    def __init__(self, world: WorldModel) -> None:
        self.world = world

    def resolve_entity(self, text: str, *, type_hint: str | None = None) -> dict[str, Any]:
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
        ent = self.world.entity(enterprise_id)
        if ent is None:
            return {
                "ontology_release_id": self.world.release_id,
                "enterprise_id": enterprise_id,
                "found": False,
            }
        return {
            "ontology_release_id": self.world.release_id,
            "found": True,
            "entity": ent.to_dict(),
            "named_graphs": self.world.named_graphs,
        }

    def get_relationships(
        self, enterprise_id: str, *, predicate: str | None = None
    ) -> dict[str, Any]:
        claims = self.world.relationships(enterprise_id, predicate=predicate)
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "claims": [c.to_dict() for c in claims],
        }

    def find_related_entities(self, enterprise_id: str) -> dict[str, Any]:
        related = self.world.related_entities(enterprise_id)
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "related": [e.to_dict() for e in related],
        }

    def search_evidence(
        self,
        *,
        query: str | None = None,
        entity_ids: list[str] | None = None,
        require_quote: bool = True,
    ) -> dict[str, Any]:
        milvus_hits = _search_evidence_milvus(query=query, entity_ids=entity_ids)
        if milvus_hits is not None:
            hits = milvus_hits
            backend = "milvus"
        else:
            hits = list(self.world.evidence)
            if entity_ids:
                wanted = set(entity_ids)
                hits = [e for e in hits if wanted & set(e.entity_ids)]
            if query:
                q = query.lower()
                hits = [
                    e for e in hits if q in e.text.lower() or (e.quote and q in e.quote.lower())
                ]
            backend = "yaml"
        if require_quote:
            # Milvus 行可能只存 quote/text 合一；YAML 路径要求独立 quote 字段
            if backend == "yaml":
                hits = [e for e in hits if e.quote]
            else:
                hits = [e for e in hits if (e.quote or e.text)]
        hits.sort(key=lambda e: (0 if e.quote else 1, -e.score))
        return {
            "ontology_release_id": self.world.release_id,
            "query": query,
            "entity_ids": entity_ids or [],
            "evidence": [e.to_dict() for e in hits],
            "policy": "evidence_first",
            "backend": backend,
        }

    def search_assets(
        self, *, query: str | None = None, entity_ids: list[str] | None = None
    ) -> dict[str, Any]:
        hits = list(self.world.assets)
        if entity_ids:
            wanted = set(entity_ids)
            hits = [a for a in hits if wanted & set(a.entity_ids)]
        if query:
            q = query.lower()

            def _match(a: AssetHit) -> bool:
                desc = (a.description or "").lower()
                return q in a.name.lower() or q in a.asset_fqn.lower() or q in desc

            hits = [a for a in hits if _match(a)]
        return {
            "ontology_release_id": self.world.release_id,
            "query": query,
            "entity_ids": entity_ids or [],
            "assets": [a.to_dict() for a in hits],
        }

    def get_entity_evidence(self, enterprise_id: str) -> dict[str, Any]:
        hits = self.world.evidence_for(enterprise_id)
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "evidence": [e.to_dict() for e in hits],
        }

    def get_entity_assets(self, enterprise_id: str) -> dict[str, Any]:
        hits = self.world.assets_for(enterprise_id)
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "assets": [a.to_dict() for a in hits],
        }

    def get_entity_context(self, enterprise_id: str) -> dict[str, Any]:
        """World Model 聚合上下文（Citationware）：entity + 关系 + 证据 claim/span + 资产。"""
        ent = self.get_entity(enterprise_id)
        if not ent.get("found"):
            return ent

        relationships = self.get_relationships(enterprise_id)["claims"]
        related = self.find_related_entities(enterprise_id)["related"]
        related_by_id = {e["enterprise_id"]: e for e in related}
        entity = ent["entity"]

        target_ids = list(entity.get("targets") or [])
        for c in relationships:
            oid = c.get("object_id")
            if c.get("predicate") == "targets" and oid and oid not in target_ids:
                target_ids.append(oid)

        disease_ids = list(entity.get("indications") or [])
        for c in relationships:
            oid = c.get("object_id")
            if c.get("predicate") == "investigates" and oid and oid not in disease_ids:
                disease_ids.append(oid)

        targets = [_entity_ref(related_by_id, tid, self.world) for tid in target_ids]
        diseases = [_entity_ref(related_by_id, did, self.world) for did in disease_ids]

        evidence_by_id = {e.evidence_id: e for e in self.world.evidence_for(enterprise_id)}
        citation_evidence = _build_citation_evidence(
            relationships,
            evidence_by_id,
            enterprise_id=enterprise_id,
        )
        # 补充仅挂在 entity_ids 上、尚未被 claim 引用的证据
        claimed_eids = {row["id"] for row in citation_evidence if row.get("id")}
        for hit in evidence_by_id.values():
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

        assets = self.get_entity_assets(enterprise_id)["assets"]
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
            # 向后兼容字段
            "relationships": relationships,
            "related_entities": related,
            "assets": assets,
        }

    def dispatch(self, op: str, **kwargs: Any) -> dict[str, Any]:
        fn = getattr(self, op, None)
        if fn is None or op.startswith("_"):
            raise KeyError(f"未知语义操作：{op}")
        return fn(**kwargs)

    def golden_path(self, candidate_key: str = "savolitinib") -> dict[str, Any]:
        """金路径：DrugCandidate → Target → Disease → Evidence → ELN/LIMS Asset。"""
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
        }


def _entity_ref(
    related_by_id: dict[str, dict[str, Any]],
    enterprise_id: str,
    world: WorldModel,
) -> dict[str, Any]:
    row = related_by_id.get(enterprise_id)
    if row is None:
        ent = world.entity(enterprise_id)
        row = ent.to_dict() if ent is not None else {"enterprise_id": enterprise_id}
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
    """Claim + Evidence span → Citationware 条目。"""
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
    if collection == "internal_docs" or doc_id.startswith("eln:"):
        return "ELN"
    if getattr(hit, "pmid", None) or doc_id.startswith("pubmed:"):
        return "PubMed"
    return collection or "Evidence"
