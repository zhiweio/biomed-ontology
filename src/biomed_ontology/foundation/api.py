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
        "summary": "聚合：entity + relationships + evidence + assets",
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
        ent = self.get_entity(enterprise_id)
        if not ent.get("found"):
            return ent
        return {
            "ontology_release_id": self.world.release_id,
            "enterprise_id": enterprise_id,
            "entity": ent["entity"],
            "relationships": self.get_relationships(enterprise_id)["claims"],
            "related_entities": self.find_related_entities(enterprise_id)["related"],
            "evidence": self.get_entity_evidence(enterprise_id)["evidence"],
            "assets": self.get_entity_assets(enterprise_id)["assets"],
        }

    def dispatch(self, op: str, **kwargs: Any) -> dict[str, Any]:
        fn = getattr(self, op, None)
        if fn is None or op.startswith("_"):
            raise KeyError(f"未知语义操作：{op}")
        return fn(**kwargs)

    def golden_path(self, candidate_key: str = "savolitinib") -> dict[str, Any]:
        """金路径：DrugCandidate → Target → Disease → Evidence → ELN Asset。"""
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
            "context": ctx,
        }
