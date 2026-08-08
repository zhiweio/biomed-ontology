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
    GRAPH_BIOMEDICAL,
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
)
from biomed_ontology.foundation.models import EvidenceHit
from biomed_ontology.foundation.obs_log import observe_retrieval
from biomed_ontology.foundation.store import (
    fetch_bios_concepts,
    fetch_claims,
    fetch_entity,
    fetch_related_ids,
)
from biomed_ontology.foundation.world import WorldModel

__all__ = ["SEMANTIC_OPS", "FoundationApi"]


class BackendUnavailableError(RuntimeError):
    """GraphDB / Milvus / OpenMetadata 不可用；禁止回落 YAML。"""


def _search_evidence_milvus(
    *,
    query: str | None,
    entity_ids: list[str] | None,
) -> list[EvidenceHit]:
    with observe_retrieval(
        "milvus.foundation_evidence",
        op="search_evidence_milvus",
        input_summary={"query": query, "entity_ids": entity_ids or []},
    ) as obs:
        obs["backend"] = "milvus"
        obs["why"] = {
            "reason": "evidence_index_required",
            "yaml_fallback": False,
            "collection": "foundation_evidence",
        }
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
            fields = [
                "evidence_id",
                "text",
                "quote",
                "entity_ids",
                "doc_id",
                "collection",
                "score",
            ]
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
                    output_fields=fields,
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
        obs["output"] = {"hit_count": len(hits)}
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
        with observe_retrieval(
            "resolver.dictionary",
            op="resolve_entity",
            input_summary={"text": text, "type_hint": type_hint},
        ) as obs:
            obs["backend"] = "resolver"
            obs["why"] = {
                "reason": "entity_resolution_dictionary",
                "yaml_wm_fallback": False,
                "note": "词典仅用于 ER，World Model 查询仍走 GraphDB/Milvus/OM",
            }
            assert self.world.resolver is not None
            hits = self.world.resolver.resolve_text(text)
            if type_hint and len(hits) == 1 and hits[0].canonical_entity is None:
                hits = [self.world.resolver.resolve_mention(text, type_hint=type_hint)]
            chosen = next((h.canonical_entity for h in hits if h.canonical_entity), None)
            obs["why"]["chosen"] = chosen
            obs["why"]["candidate_count"] = len(hits)
            obs["output"] = {"resolved_count": len(hits), "chosen": chosen}
            return {
                "ontology_release_id": self.world.release_id,
                "query": text,
                "resolved": [h.to_dict() for h in hits],
                "backend": "resolver",
            }

    def get_entity(self, enterprise_id: str) -> dict[str, Any]:
        with observe_retrieval(
            "graphdb.ontology",
            op="get_entity",
            input_summary={"enterprise_id": enterprise_id},
        ) as obs:
            obs["backend"] = "graphdb"
            obs["why"] = {"graph": GRAPH_ONTOLOGY, "yaml_fallback": False}
            gdb = self._require_graphdb()
            try:
                ent = fetch_entity(gdb, enterprise_id)
            except Exception as exc:
                raise BackendUnavailableError(f"GraphDB 读实体失败：{exc}") from exc
            if ent is None:
                obs["output"] = {"found": False}
                return {
                    "ontology_release_id": self.world.release_id,
                    "enterprise_id": enterprise_id,
                    "found": False,
                    "backend": "graphdb",
                }
            obs["output"] = {"found": True, "kind": ent.entity_kind}
            return {
                "ontology_release_id": self.world.release_id,
                "found": True,
                "entity": ent.to_dict(),
                "named_graphs": {
                    "ontology": GRAPH_ONTOLOGY,
                    "knowledge": GRAPH_KNOWLEDGE,
                    "provenance": GRAPH_PROVENANCE,
                    "biomedical": GRAPH_BIOMEDICAL,
                },
                "backend": "graphdb",
            }

    def get_relationships(
        self, enterprise_id: str, *, predicate: str | None = None
    ) -> dict[str, Any]:
        with observe_retrieval(
            "graphdb.provenance+knowledge",
            op="get_relationships",
            input_summary={"enterprise_id": enterprise_id, "predicate": predicate},
        ) as obs:
            obs["backend"] = "graphdb"
            obs["why"] = {
                "graphs": [GRAPH_PROVENANCE, GRAPH_KNOWLEDGE],
                "yaml_fallback": False,
            }
            gdb = self._require_graphdb()
            try:
                claims = fetch_claims(gdb, enterprise_id, predicate=predicate)
            except Exception as exc:
                raise BackendUnavailableError(f"GraphDB 读关系失败：{exc}") from exc
            obs["output"] = {"claim_count": len(claims)}
            return {
                "ontology_release_id": self.world.release_id,
                "enterprise_id": enterprise_id,
                "claims": [c.to_dict() for c in claims],
                "backend": "graphdb",
            }

    def find_related_entities(self, enterprise_id: str) -> dict[str, Any]:
        with observe_retrieval(
            "graphdb.related",
            op="find_related_entities",
            input_summary={"enterprise_id": enterprise_id},
        ) as obs:
            obs["backend"] = "graphdb"
            obs["why"] = {"yaml_fallback": False}
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
            obs["output"] = {"related_count": len(related)}
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
        with observe_retrieval(
            "milvus.evidence_index",
            op="search_evidence",
            input_summary={
                "query": query,
                "entity_ids": entity_ids or [],
                "require_quote": require_quote,
            },
        ) as obs:
            obs["backend"] = "milvus"
            obs["why"] = {"yaml_fallback": False, "policy": "evidence_first"}
            hits = _search_evidence_milvus(query=query, entity_ids=entity_ids)
            if require_quote:
                hits = [e for e in hits if (e.quote or e.text)]
            hits.sort(key=lambda e: (0 if e.quote else 1, -e.score))
            obs["output"] = {"evidence_count": len(hits)}
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
        with observe_retrieval(
            "openmetadata.glossary",
            op="search_assets",
            input_summary={"query": query, "entity_ids": entity_ids or []},
        ) as obs:
            obs["backend"] = "openmetadata"
            obs["why"] = {"yaml_fallback": False, "glossary": "HMDEnterpriseAssets"}
            try:
                self.openmetadata.ping()
                hits = self.openmetadata.search_assets(query=query, entity_ids=entity_ids)
            except Exception as exc:
                raise BackendUnavailableError(f"OpenMetadata 不可用：{exc}") from exc
            obs["output"] = {"asset_count": len(hits)}
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
        """强制 GraphDB + Milvus + OM + BIOS biomedical；无 YAML fallback。"""
        with observe_retrieval(
            "context.aggregate",
            op="get_entity_context",
            input_summary={"enterprise_id": enterprise_id},
        ) as obs:
            ent = self.get_entity(enterprise_id)
            if not ent.get("found"):
                obs["backends"] = {"entity": "graphdb"}
                obs["why"] = {"found": False, "yaml_fallback": False}
                obs["output"] = {"found": False}
                return ent

            rel = self.get_relationships(enterprise_id)
            relationships = rel["claims"]
            related_resp = self.find_related_entities(enterprise_id)
            related = related_resp["related"]
            related_by_id = {e["enterprise_id"]: e for e in related}
            entity = ent["entity"]
            kind = str(entity.get("entity_kind") or "")

            target_ids = list(entity.get("targets") or [])
            disease_ids = list(entity.get("indications") or [])
            drug_ids: list[str] = []

            for c in relationships:
                sid, oid, pred = c.get("subject_id"), c.get("object_id"), c.get("predicate")
                if pred == "targets":
                    if sid == enterprise_id and oid and oid not in target_ids:
                        target_ids.append(oid)
                    if oid == enterprise_id and sid and sid not in drug_ids:
                        drug_ids.append(sid)
                if pred == "investigates":
                    if sid == enterprise_id and oid and oid not in disease_ids:
                        disease_ids.append(oid)
                    if oid == enterprise_id and sid and sid not in drug_ids:
                        drug_ids.append(sid)
                if pred == "associatedWith":
                    if sid == enterprise_id and oid and oid not in disease_ids:
                        disease_ids.append(oid)
                    if oid == enterprise_id and sid and sid not in target_ids:
                        target_ids.append(sid)

            targets = [_entity_ref(related_by_id, tid, self) for tid in target_ids]
            diseases = [_entity_ref(related_by_id, did, self) for did in disease_ids]
            drugs = [_entity_ref(related_by_id, did, self) for did in drug_ids]

            # Evidence / assets：根实体 + 一跳相关（仍走 Milvus / OM）
            ev_scope = list(dict.fromkeys([enterprise_id, *target_ids, *disease_ids, *drug_ids]))
            ev_out = self.search_evidence(entity_ids=ev_scope, require_quote=True)
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

            asset_scope = list(dict.fromkeys([enterprise_id, *drug_ids]))
            assets_out = self.search_assets(entity_ids=asset_scope)
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

            # BIOS：exactMatch → GraphDB biomedical 命名图
            xref_pool: list[str] = list(entity.get("exact_match_xrefs") or [])
            for ref in [*targets, *diseases, *drugs]:
                xref_pool.extend(ref.get("external_ids") or [])
            gdb = self._require_graphdb()
            bios_bridges = fetch_bios_concepts(gdb, xref_pool)
            for t in targets:
                t["bios"] = [
                    b for b in bios_bridges if b.get("bios_curie") in (t.get("external_ids") or [])
                ]
            for d in diseases:
                d["bios"] = [
                    b for b in bios_bridges if b.get("bios_curie") in (d.get("external_ids") or [])
                ]
            for d in drugs:
                d["bios"] = [
                    b for b in bios_bridges if b.get("bios_curie") in (d.get("external_ids") or [])
                ]

            backends = {
                "entity": "graphdb",
                "relationships": "graphdb",
                "related": "graphdb",
                "evidence": "milvus",
                "assets": "openmetadata",
                "bios": "graphdb_biomedical" if bios_bridges else "graphdb_biomedical_empty",
            }
            obs["backends"] = backends
            obs["why"] = {
                "yaml_fallback": False,
                "entity_kind": kind,
                "bios_bridge_count": len(bios_bridges),
                "bios_graph": GRAPH_BIOMEDICAL,
            }
            obs["output"] = {
                "targets": len(targets),
                "diseases": len(diseases),
                "drugs": len(drugs),
                "evidence": len(citation_evidence),
                "assets": len(internal_assets),
                "bios": len(bios_bridges),
            }
            return {
                "ontology_release_id": self.world.release_id,
                "enterprise_id": enterprise_id,
                "entity": entity,
                "entity_kind": kind,
                "targets": targets,
                "diseases": diseases,
                "drugs": drugs,
                "evidence": citation_evidence,
                "internal_assets": internal_assets,
                "bios_bridges": bios_bridges,
                "relationships": relationships,
                "related_entities": related,
                "assets": assets,
                "backends": backends,
            }

    def dispatch(self, op: str, **kwargs: Any) -> dict[str, Any]:
        fn = getattr(self, op, None)
        if fn is None or op.startswith("_"):
            raise KeyError(f"未知语义操作：{op}")
        return fn(**kwargs)

    def golden_path(
        self,
        candidate_key: str = "savolitinib",
        *,
        tools: Any | None = None,
    ) -> dict[str, Any]:
        """WM 金路径；若传入 ToolApi，追加文献 search + restore 腿。"""
        with observe_retrieval(
            "golden_path",
            op="golden_path",
            input_summary={"candidate": candidate_key, "with_kb": tools is not None},
        ) as obs:
            resolve = self.resolve_entity(candidate_key)
            canonical = next(
                (r["canonical_entity"] for r in resolve["resolved"] if r.get("canonical_entity")),
                None,
            )
            if not canonical:
                obs["why"] = {"reason": "candidate_unresolved", "yaml_fallback": False}
                obs["output"] = {"ok": False}
                return {"ok": False, "reason": "candidate_unresolved", "resolve": resolve}
            ctx = self.get_entity_context(canonical)
            kind = str(ctx.get("entity_kind") or ctx.get("entity", {}).get("entity_kind") or "")
            path = _path_for_kind(kind)
            backends = ctx.get("backends") or {}
            kb_leg = _kb_golden_leg(tools, candidate_key) if tools is not None else None
            # WM 段走完即基础 ok；传入 tools 时必须文献腿也过
            kb_ok = True if kb_leg is None else bool(kb_leg.get("ok"))
            ok = kb_ok and bool(ctx.get("entity") or ctx.get("found", True))
            obs["backends"] = backends
            obs["why"] = {
                "yaml_fallback": False,
                "canonical": canonical,
                "entity_kind": kind,
                "path": path,
                "bios_used": bool(ctx.get("bios_bridges")),
                "kb_leg": kb_leg,
            }
            obs["output"] = {
                "ok": ok,
                "targets": len(ctx.get("targets") or []),
                "diseases": len(ctx.get("diseases") or []),
                "drugs": len(ctx.get("drugs") or []),
                "evidence": len(ctx.get("evidence") or []),
                "assets": len(ctx.get("internal_assets") or []),
                "bios": len(ctx.get("bios_bridges") or []),
                "kb_hits": (kb_leg or {}).get("hit_count"),
            }
            return {
                "ok": ok,
                "path": path,
                "canonical_entity": canonical,
                "entity_kind": kind,
                "query": candidate_key,
                "resolve": resolve,
                "context": ctx,
                "backends": backends,
                "kb": kb_leg,
                "evaluation": {
                    "yaml_fallback": False,
                    "backends_ok": _backends_ok(backends),
                    "bios_graphdb": bool(ctx.get("bios_bridges")),
                    "milvus_evidence": len(ctx.get("evidence") or []) > 0,
                    "openmetadata_assets": len(ctx.get("internal_assets") or []) > 0
                    or kind not in {"DrugCandidate"},
                    "kb_search_nonempty": kb_ok if kb_leg is not None else None,
                    "kb_restore_ok": (kb_leg or {}).get("restore_ok"),
                },
            }


def _kb_golden_leg(tools: Any, query: str) -> dict[str, Any]:
    """文献腿：search_documents → restore_context。"""
    try:
        search = tools.search_documents(query, top_k=5)
        hits = search.get("results") or []
        restore_ok = False
        chunk_id = None
        if hits:
            chunk_id = hits[0].get("chunk_id")
            if chunk_id:
                restored = tools.restore_context(chunk_id)
                restore_ok = not bool(restored.get("error")) and (
                    bool(restored.get("document") or restored.get("sections") or restored.get("text"))
                    or restored.get("ok") is True
                    or "chunk_id" in restored
                )
        return {
            "ok": len(hits) >= 1,
            "hit_count": len(hits),
            "chunk_id": chunk_id,
            "restore_ok": restore_ok,
            "query": query,
        }
    except Exception as exc:  # noqa: BLE001 — 金路径必须显式失败
        return {
            "ok": False,
            "error": str(exc),
            "hit_count": 0,
            "restore_ok": False,
            "query": query,
        }


def _path_for_kind(kind: str) -> str:
    if kind == "Target":
        return "Target→DrugCandidate→Disease→Evidence→Asset"
    if kind == "Indication":
        return "Indication→DrugCandidate→Target→Evidence→Asset"
    return "DrugCandidate→Target→Disease→Evidence→Asset"


def _backends_ok(backends: dict[str, Any]) -> bool:
    want = {
        "entity": "graphdb",
        "relationships": "graphdb",
        "evidence": "milvus",
        "assets": "openmetadata",
    }
    if any(v == "yaml" for v in backends.values() if isinstance(v, str)):
        return False
    return all(backends.get(k) == v for k, v in want.items())


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
