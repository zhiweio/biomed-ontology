"""Agent 接入层（L6）：10 个工具的统一实现。

每次调用都强制走同一条包裹链：
    契约校验入参 → 起 trace → 执行 → license 闸门 → 契约校验出参 → 落 I/O 记录

包裹链做成不可绕过（`_invoke` 是唯一入口）而不是各工具自觉调用，
因为"某个工具忘了埋点"这件事只会在需要排障时才被发现，
而那正是最不能缺埋点的时刻。

所有响应都带 `ontology_release_id`：同一个问题在 v1 与 v2 下答案可以不同，
不带版本号的答案无法复现，也就无法争论对错。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.agentapi.citation import build_evidence_tree
from biomed_ontology.licensing import tier_rank
from biomed_ontology.observability import (
    ObservabilityHub,
    ToolIoRecord,
    TraceContext,
)
from biomed_ontology.observability.contracts import ContractValidator, LicenseGate
from biomed_ontology.ontology.rdf import SPARQL_TEMPLATES, curie_to_iri
from biomed_ontology.pipeline import KnowledgeBase
from biomed_ontology.search import OPEN_RANK, HybridSearcher
from biomed_ontology.search.backends import LicenseScope

__all__ = ["TOOL_SPECS", "AgentApi", "Feedback", "ToolError"]

TOOL_VERSION = "0.1.0"


class ToolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Feedback:
    feedback_id: str
    trace_id: str | None
    verdict: str
    subject_id: str | None
    expected_id: str | None
    comment: str | None
    agent_id: str | None
    query: str | None = None


# 工具清单。与 schema/hmd_agentapi.yaml 一一对应，供 MCP / OpenAPI 自动生成。
TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "normalize_entity",
        "request": "NormalizeRequest",
        "response": "NormalizeResponse",
        "summary": "自由文本 → 唯一 code，返回归一化阶段与备选义项",
    },
    {
        "name": "resolve_alias",
        "request": "NormalizeRequest",
        "response": "NormalizeResponse",
        "summary": "单个别名的精确解析，不做文档级 NER",
    },
    {
        "name": "expand_concept",
        "request": "ExpandRequest",
        "response": "ExpandResponse",
        "summary": "概念 → 加权检索词表（同义词 + 下位词）",
    },
    {
        "name": "get_concept",
        "request": "ExpandRequest",
        "response": "ExpandResponse",
        "summary": "概念详情：标签、定义、父子、外部映射、许可等级",
    },
    {
        "name": "search_documents",
        "request": "SearchRequest",
        "response": "SearchResponse",
        "summary": "本体增强混合检索，返回带 section/page 的可溯源片段",
    },
    {
        "name": "get_facts",
        "request": "FactsRequest",
        "response": "FactsResponse",
        "summary": "结构化事实 + 语句级出处",
    },
    {
        "name": "sparql_query",
        "request": "SparqlRequest",
        "response": "SparqlResponse",
        "summary": "受控 SPARQL 模板查询，自动注入许可可见图",
    },
    {
        "name": "get_landscape",
        "request": "LandscapeRequest",
        "response": "LandscapeResponse",
        "summary": "管线矩阵：靶点 × 适应症 × 在研药物",
    },
    {
        "name": "find_analogous",
        "request": "ExpandRequest",
        "response": "ExpandResponse",
        "summary": "同靶点/同机制的类比资产，用于竞争格局与适应症拓展",
    },
    {
        "name": "submit_feedback",
        "request": "FeedbackRequest",
        "response": "FeedbackResponse",
        "summary": "回写判定结果，驱动本体演进闭环",
    },
    {
        "name": "restore_context",
        "request": "RestoreRequest",
        "response": "RestoreResponse",
        "summary": "碎片 → 原文：还原所在章节全文、面包屑与原始页码",
    },
]


@dataclass
class AgentApi:
    kb: KnowledgeBase
    searcher: HybridSearcher
    validator: ContractValidator = field(default_factory=ContractValidator)
    feedback_log: list[Feedback] = field(default_factory=list)

    @classmethod
    def from_kb(cls, kb: KnowledgeBase) -> AgentApi:
        return cls(kb=kb, searcher=HybridSearcher(kb))

    @property
    def hub(self) -> ObservabilityHub:
        return self.kb.hub

    # ------------------------------------------------------------ 包裹链

    def _invoke(
        self,
        tool_name: str,
        payload: dict[str, Any],
        handler: Callable[[TraceContext], tuple[dict[str, Any], int, LicenseTierEnum]],
        *,
        agent_id: str | None,
        session_id: str | None,
        entitlements: frozenset[str],
        trace_id: str | None,
    ) -> dict[str, Any]:
        spec = next((s for s in TOOL_SPECS if s["name"] == tool_name), None)
        if spec is None:
            raise ToolError(f"未注册的工具：{tool_name}", code="UNKNOWN_TOOL")

        ctx = self.hub.start_trace(
            release_id=self.kb.release_id,
            agent_id=agent_id,
            session_id=session_id,
            entitlements=entitlements,
            trace_id=trace_id,
        )
        started = time.perf_counter()
        # 剥掉 None：契约里"未指定"与"显式传空"是两回事，
        # 把未填参数当空值发出去会让枚举类型的校验全部失败。
        payload = {k: v for k, v in payload.items() if v is not None}
        req_check = self.validator.validate(spec["request"], payload)
        status, error, filtered, max_tier = "OK", None, 0, LicenseTierEnum.TIER_0
        body: dict[str, Any] = {}
        try:
            if not req_check.valid:
                raise ToolError("; ".join(req_check.errors), code="CONTRACT_VIOLATION")
            body, filtered, max_tier = handler(ctx)
        except ToolError as exc:
            status, error = exc.code, str(exc)
        except Exception as exc:  # 未预期异常同样要留痕，否则排障时只剩下客户端的超时
            status, error = "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"

        elapsed = (time.perf_counter() - started) * 1000
        envelope = {
            "trace_id": ctx.trace_id,
            "ontology_release_id": self.kb.release_id,
            "tool_name": tool_name,
            "tool_version": TOOL_VERSION,
            "elapsed_ms": round(elapsed, 3),
            "license_tier_max": max_tier.value,
            "license_filtered_count": filtered,
            "warnings": [] if status == "OK" else [f"{status}: {error}"],
            **body,
        }
        resp_check = self.validator.validate(spec["response"], envelope)
        self.hub.commit(
            ctx,
            ToolIoRecord(
                trace_id=ctx.trace_id,
                tool_name=tool_name,
                ontology_release_id=self.kb.release_id,
                input_json=json.dumps(payload, ensure_ascii=False, default=str),
                output_json=json.dumps(envelope, ensure_ascii=False, default=str),
                latency_ms=round(elapsed, 3),
                status=status,
                tool_version=TOOL_VERSION,
                agent_id=agent_id,
                session_id=session_id,
                error_message=error,
                contract_valid=req_check.valid and resp_check.valid,
                contract_errors=[*req_check.errors, *resp_check.errors],
                license_filtered_count=filtered,
                caller_entitlements=sorted(entitlements),
                max_tier_returned=max_tier,
            ),
        )
        if not resp_check.valid:
            envelope["warnings"] = [
                *envelope["warnings"],
                *(f"RESPONSE_CONTRACT: {e}" for e in resp_check.errors),
            ]
        return envelope

    # ------------------------------------------------------------ 1-2 归一化

    def normalize_entity(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        context: str | None = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
        detect_spans: bool = True,
        agent_id: str | None = None,
        session_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "text": text,
            "entity_types": entity_types or [],
            "context": context,
            "top_k": top_k,
            "min_confidence": min_confidence,
            "detect_spans": detect_spans,
        }

        def handler(ctx: TraceContext):
            res = self.kb.normalizer.normalize(
                text,
                ctx=ctx,
                entity_types=entity_types,
                context=context,
                top_k=top_k,
                min_confidence=min_confidence,
                detect=detect_spans,
            )
            return (
                {
                    "matched_concepts": [_match_json(m) for m in res.matched],
                    "unmapped_spans": res.unmapped_spans,
                    "llm_invoked": res.llm_invoked,
                },
                0,
                LicenseTierEnum.TIER_0,
            )

        return self._invoke(
            "normalize_entity",
            payload,
            handler,
            agent_id=agent_id,
            session_id=session_id,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    def resolve_alias(self, alias: str, **kw: Any) -> dict[str, Any]:
        """单别名精确解析。与 normalize_entity 的差别是不做 span 检测 ——
        调用方已经知道自己拿的是一个术语，再跑 NER 只会引入新的切分误差。"""
        kw.setdefault("detect_spans", False)
        out = self.normalize_entity(alias, **kw)
        out["tool_name"] = "resolve_alias"
        return out

    # ------------------------------------------------------------ 3-4 概念

    def expand_concept(
        self,
        concept_id: str,
        *,
        max_depth: int = 2,
        include_descendants: bool = True,
        min_weight: float = 0.1,
        languages: list[str] | None = None,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "concept_id": concept_id,
            "max_depth": max_depth,
            "include_descendants": include_descendants,
            "min_weight": min_weight,
            "languages": languages or [],
        }

        def handler(ctx: TraceContext):
            if self.kb.concept(concept_id) is None:
                raise ToolError(f"概念不存在：{concept_id}", code="NOT_FOUND")
            terms = self.kb.normalizer.expand(
                concept_id,
                ctx=ctx,
                max_depth=max_depth,
                include_descendants=include_descendants,
                min_weight=min_weight,
                languages=languages,
            )
            return (
                {
                    "concept_id": concept_id,
                    "expansion_size": len(terms),
                    "expansion_terms": [
                        {
                            "term": t.term,
                            "weight": round(t.weight, 4),
                            "concept_id": t.concept_id,
                            "alias_id": t.alias_id,
                            "scope": t.scope,
                            "depth": t.depth,
                            "lang": t.lang,
                        }
                        for t in terms
                    ],
                },
                0,
                LicenseTierEnum.TIER_0,
            )

        return self._invoke(
            "expand_concept",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    def get_concept(
        self,
        concept_id: str,
        *,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"concept_id": concept_id}

        def handler(ctx: TraceContext):
            c = self.kb.concept(concept_id)
            if c is None:
                raise ToolError(f"概念不存在：{concept_id}", code="NOT_FOUND")
            aliases = [s for s in self.kb.synonyms if s.concept_id == concept_id]
            children = [x.concept_id for x in self.kb.concepts if concept_id in x.parents]
            return (
                {
                    "concept_id": concept_id,
                    "expansion_size": len(aliases),
                    "expansion_terms": [
                        {
                            "term": s.alias_raw,
                            "weight": 1.0,
                            "concept_id": concept_id,
                            "alias_id": s.alias_id,
                            "scope": s.scope.value,
                            "depth": 0,
                            "lang": s.lang.value,
                        }
                        for s in aliases
                    ],
                    "concept_detail": {
                        "entity_type": c.entity_type.value,
                        "preferred_label_en": c.preferred_label_en,
                        "preferred_label_zh": c.preferred_label_zh,
                        "definition": c.definition,
                        "parents": list(c.parents),
                        "children": children,
                        "license_tier": c.license_tier.value,
                        "review_status": c.review_status.value,
                    },
                },
                0,
                c.license_tier,
            )

        return self._invoke(
            "get_concept",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 5 检索

    def search_documents(
        self,
        query: str,
        *,
        top_k: int = 10,
        expand: bool = True,
        channels: list[str] | None = None,
        labels: list[str] | None = None,
        modalities: list[str] | None = None,
        max_tier: str = "TIER_3",
        agent_id: str | None = None,
        session_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "top_k": top_k,
            "use_expansion": expand,
            "channels": channels or [],
            "labels": labels or [],
            "modalities": modalities or [],
            "max_tier": max_tier,
        }

        def handler(ctx: TraceContext):
            chans = tuple(RetrievalChannelEnum(c) for c in (channels or ["BM25", "DENSE", "GRAPH"]))
            hits, filtered = self.searcher.search(
                query,
                ctx=ctx,
                top_k=top_k,
                entitlements=entitlements,
                max_tier=LicenseTierEnum(max_tier),
                expand=expand,
                channels=chans,
                labels=labels,
                modalities=tuple(modalities or ()),
            )
            max_returned = max(
                (h.license_tier for h in hits), key=tier_rank, default=LicenseTierEnum.TIER_0
            )
            return (
                {
                    "results": [_hit_json(h) for h in hits],
                    "total": len(hits),
                    # 扁平列表里同一文档的 5 个碎片看着像 5 条独立证据，
                    # 实际可能全出自同一段 —— 证据树消除这种数量错觉。
                    "evidence_tree": build_evidence_tree(self.kb, hits),
                },
                filtered,
                max_returned,
            )

        return self._invoke(
            "search_documents",
            payload,
            handler,
            agent_id=agent_id,
            session_id=session_id,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 6 事实

    def get_facts(
        self,
        *,
        subject_id: str | None = None,
        predicate: str | None = None,
        object_id: str | None = None,
        min_confidence: float = 0.0,
        include_evidence: bool = True,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
            "min_confidence": min_confidence,
            "include_evidence": include_evidence,
        }

        def handler(ctx: TraceContext):
            with ctx.span("get_facts"):
                sel = [
                    f
                    for f in self.kb.facts
                    if (subject_id is None or f.subject_id == subject_id)
                    and (predicate is None or f.predicate.value == predicate)
                    and (object_id is None or f.object_id == object_id)
                    and f.confidence >= min_confidence
                ]
                gate = LicenseGate(entitlements)
                res = gate.filter(
                    sel,
                    tier_of=lambda f: f.license_tier,
                    source_of=lambda f: _fact_source(self.kb, f),
                )
                max_returned = max(
                    (f.license_tier for f in res.kept),
                    key=tier_rank,
                    default=LicenseTierEnum.TIER_0,
                )
            return (
                {
                    "facts": [_fact_json(self.kb, f, include_evidence) for f in res.kept],
                    "total": len(res.kept),
                },
                res.filtered_count,
                max_returned,
            )

        return self._invoke(
            "get_facts",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 7 SPARQL

    def sparql_query(
        self,
        template: str,
        *,
        bindings: dict[str, str] | None = None,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "sparql_template": template,
            "bindings": sorted(f"{k}={v}" for k, v in (bindings or {}).items()),
        }

        def handler(ctx: TraceContext):
            # 只接受预置模板，不接受任意 SPARQL：开放任意查询等于把
            # license 重写这道防线交给调用方自觉遵守，而它是不该被绕过的。
            if template not in SPARQL_TEMPLATES:
                raise ToolError(
                    f"未知模板 {template}，可用：{sorted(SPARQL_TEMPLATES)}",
                    code="UNKNOWN_TEMPLATE",
                )
            sparql = SPARQL_TEMPLATES[template]
            needed = set(re.findall(r"%\((\w+)\)s", sparql))
            given = {k: curie_to_iri(v) for k, v in (bindings or {}).items()}
            if missing := needed - set(given):
                raise ToolError(
                    f"模板 {template} 缺少绑定：{sorted(missing)}", code="MISSING_BINDING"
                )
            sparql = sparql % {k: given[k] for k in needed} if needed else sparql
            with ctx.span("sparql", **{"hmd.template": template}):
                rows = self.kb.graph.query(sparql, entitlements=entitlements)
            return (
                {
                    "rows": [json.dumps(r, ensure_ascii=False, default=str) for r in rows],
                    "total": len(rows),
                },
                0,
                LicenseTierEnum.TIER_0,
            )

        return self._invoke(
            "sparql_query",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 8 管线矩阵

    def get_landscape(
        self,
        *,
        target_id: str | None = None,
        disease_id: str | None = None,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"target_id": target_id, "disease_id": disease_id}

        def handler(ctx: TraceContext):
            with ctx.span("landscape"):
                gate = LicenseGate(entitlements)
                targets: dict[str, set[str]] = {}
                for f in self.kb.facts:
                    if f.predicate.value in {"inhibits", "has_target"} and f.object_id:
                        targets.setdefault(f.subject_id, set()).add(f.object_id)

                # 先按条目过滤再聚合行。反过来做会因为行内混了一条 TIER_3 事实
                # 就把整行连同其中的公开信息一起藏掉 —— 那是把许可控制做成了信息删减。
                entries: list[tuple[str, str, dict[str, Any], str | None]] = []
                for f in self.kb.facts:
                    if f.predicate.value not in {"treats", "in_clinical_trial_for"}:
                        continue
                    if not f.object_id or (disease_id and f.object_id != disease_id):
                        continue
                    for tgt in sorted(targets.get(f.subject_id, {"UNKNOWN"})):
                        if target_id and tgt != target_id:
                            continue
                        entries.append(
                            (
                                tgt,
                                f.object_id,
                                {
                                    "concept_id": f.subject_id,
                                    "label": _label(self.kb, f.subject_id),
                                    "stage": f.predicate.value,
                                    "license_tier": f.license_tier.value,
                                },
                                _fact_source(self.kb, f),
                            )
                        )

                res = gate.filter(
                    entries,
                    tier_of=lambda e: LicenseTierEnum(e[2]["license_tier"]),
                    source_of=lambda e: e[3],
                )

                rows: dict[tuple[str, str], dict[str, Any]] = {}
                for tgt, dis, entry, _src in res.kept:
                    row = rows.setdefault(
                        (tgt, dis),
                        {
                            "target_id": None if tgt == "UNKNOWN" else tgt,
                            "target_label": _label(self.kb, tgt),
                            "disease_id": dis,
                            "disease_label": _label(self.kb, dis),
                            "substances": [],
                            "license_tier": LicenseTierEnum.TIER_0.value,
                        },
                    )
                    if entry not in row["substances"]:
                        row["substances"].append(entry)
                    if tier_rank(LicenseTierEnum(entry["license_tier"])) > tier_rank(
                        LicenseTierEnum(row["license_tier"])
                    ):
                        row["license_tier"] = entry["license_tier"]

                ordered = [rows[k] for k in sorted(rows)]
                max_returned = max(
                    (LicenseTierEnum(r["license_tier"]) for r in ordered),
                    key=tier_rank,
                    default=LicenseTierEnum.TIER_0,
                )
            return (
                {"landscape_rows": ordered, "total": len(ordered)},
                res.filtered_count,
                max_returned,
            )

        return self._invoke(
            "get_landscape",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 9 类比资产

    def find_analogous(
        self,
        concept_id: str,
        *,
        top_k: int = 10,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"concept_id": concept_id, "top_k": top_k}

        def handler(ctx: TraceContext):
            if self.kb.concept(concept_id) is None:
                raise ToolError(f"概念不存在：{concept_id}", code="NOT_FOUND")
            with ctx.span("find_analogous"):
                mine = {
                    f.object_id
                    for f in self.kb.facts
                    if f.subject_id == concept_id
                    and f.predicate.value in {"inhibits", "has_target"}
                    and f.object_id
                }
                scored: list[tuple[str, float, list[str]]] = []
                for other in {f.subject_id for f in self.kb.facts} - {concept_id}:
                    theirs = {
                        f.object_id
                        for f in self.kb.facts
                        if f.subject_id == other
                        and f.predicate.value in {"inhibits", "has_target"}
                        and f.object_id
                    }
                    if not mine or not theirs:
                        continue
                    shared = mine & theirs
                    if not shared:
                        continue
                    # Jaccard 而非交集大小：靶点多的药物不该仅因为"靶点多"就更像。
                    scored.append((other, len(shared) / len(mine | theirs), sorted(shared)))
                scored.sort(key=lambda x: (-x[1], x[0]))
            return (
                {
                    "concept_id": concept_id,
                    "expansion_size": len(scored),
                    "expansion_terms": [
                        {
                            "term": _label(self.kb, cid),
                            "weight": round(sc, 4),
                            "concept_id": cid,
                            "alias_id": None,
                            "scope": "RELATED",
                            "depth": 1,
                            "lang": "en",
                            "shared_targets": shared,
                        }
                        for cid, sc, shared in scored[:top_k]
                    ],
                },
                0,
                LicenseTierEnum.TIER_0,
            )

        return self._invoke(
            "find_analogous",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 10 反馈

    def submit_feedback(
        self,
        verdict: str,
        *,
        source_trace_id: str,
        offending_concept_id: str | None = None,
        expected_concept_id: str | None = None,
        reason: str | None = None,
        free_text: str | None = None,
        query: str | None = None,
        agent_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            # 反馈以被评价那次调用的 trace_id 为主键，而非本次提交的 trace_id：
            # 只有挂回原调用，才能把"错在哪一步"与当时的候选集对上。
            "trace_id": source_trace_id,
            "verdict": verdict,
            "reason": reason,
            "expected_concept_id": expected_concept_id,
            "offending_concept_id": offending_concept_id,
            "free_text": free_text,
            "query": query,
        }

        def handler(ctx: TraceContext):
            fb = Feedback(
                feedback_id=f"FB:{len(self.feedback_log) + 1:06d}",
                trace_id=source_trace_id,
                verdict=verdict,
                subject_id=offending_concept_id,
                expected_id=expected_concept_id,
                comment=free_text or reason,
                agent_id=agent_id,
                query=query,
            )
            self.feedback_log.append(fb)
            return (
                {"accepted": True, "signal_id": None},
                0,
                LicenseTierEnum.TIER_0,
            )

        return self._invoke(
            "submit_feedback",
            payload,
            handler,
            agent_id=agent_id,
            session_id=None,
            entitlements=entitlements,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------ 11 引用还原

    def restore_context(
        self,
        chunk_id: str,
        *,
        restore_scope: str = "SECTION",
        max_chars: int = 8000,
        agent_id: str | None = None,
        session_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "chunk_id": chunk_id,
            "restore_scope": restore_scope,
            "max_chars": max_chars,
        }

        def handler(ctx: TraceContext):
            from biomed_ontology.agentapi.citation import restore_context as _restore

            scope = LicenseScope(
                max_rank=tier_rank(LicenseTierEnum.TIER_3),
                open_rank=OPEN_RANK,
                entitled_sources=entitlements,
            )
            try:
                restored = _restore(
                    self.kb,
                    chunk_id,
                    scope=restore_scope,
                    max_chars=max_chars,
                    # 复用检索那一个谓词。这里另写一份判断就会出现
                    # "检索看不到但还原看得到"的越权。
                    permits=scope.permits,
                )
            except KeyError as exc:
                raise ToolError(str(exc), code="NOT_FOUND") from exc
            except PermissionError as exc:
                raise ToolError(str(exc), code="LICENSE_DENIED") from exc

            with ctx.span("restore_context", chunk_id=chunk_id, scope=str(restore_scope)):
                pass
            return (
                {
                    "doc_id": restored.doc_id,
                    "section_id": restored.section_id,
                    "section_path": restored.section_path,
                    "breadcrumb": restored.breadcrumb,
                    "full_text": restored.full_text,
                    "page_start": restored.page_start,
                    "page_end": restored.page_end,
                    "sibling_paths": restored.sibling_paths,
                    "truncated": restored.truncated,
                    "restored_chunk_ids": restored.restored_chunk_ids,
                },
                0,
                restored.license_tier,
            )

        return self._invoke(
            "restore_context",
            payload,
            handler,
            agent_id=agent_id,
            session_id=session_id,
            entitlements=entitlements,
            trace_id=trace_id,
        )


# ---------------------------------------------------------------- 序列化


def _match_json(m: Any) -> dict[str, Any]:
    return {
        "concept_id": m.concept_id,
        "matched_text": m.matched_text,
        "stage": m.stage.value if hasattr(m.stage, "value") else str(m.stage),
        "confidence": round(m.confidence, 4),
        "char_start": m.char_start,
        "char_end": m.char_end,
        "preferred_label_en": m.preferred_label_en,
        "preferred_label_zh": m.preferred_label_zh,
        "entity_type": m.entity_type.value if hasattr(m.entity_type, "value") else m.entity_type,
        "is_ambiguous": m.is_ambiguous,
        "rationale": m.rationale,
        "alternatives": [
            {"concept_id": a[0], "score": round(a[1], 4)} if isinstance(a, tuple) else a
            for a in (m.alternatives or [])
        ],
    }


def _hit_json(h: Any) -> dict[str, Any]:
    return {
        "chunk_id": h.chunk_id,
        "doc_id": h.doc_id,
        "score": h.score,
        "retrieval_channel": h.channel.value,
        "section": h.section,
        "page": h.page,
        "snippet": h.snippet,
        "license_tier": h.license_tier.value,
        "concept_ids": h.matched_concepts,
        "labels": h.labels,
        "modality": h.modality,
        "explain": h.explain,
    }


def _fact_json(kb: KnowledgeBase, f: Any, include_evidence: bool) -> dict[str, Any]:
    out = {
        "fact_id": f.fact_id,
        "subject_id": f.subject_id,
        "subject_label": _label(kb, f.subject_id),
        "predicate": f.predicate.value,
        "object_id": f.object_id,
        "object_label": _label(kb, f.object_id) if f.object_id else None,
        "object_value": f.object_value,
        "object_unit": f.object_unit,
        "qualifiers": f.qualifiers,
        "confidence": round(f.confidence, 4),
        "modality": f.modality.value,
        "license_tier": f.license_tier.value,
        "review_status": f.review_status.value,
    }
    if include_evidence:
        out["evidence"] = [
            {
                "chunk_id": e.chunk_id,
                "doc_id": e.doc_id,
                "section": e.section,
                "page": e.page,
                "quote": e.quote,
                "title": (d.title if (d := kb.document(e.doc_id)) else None),
            }
            for e in f.evidence
        ]
    return out


def _label(kb: KnowledgeBase, concept_id: str | None) -> str | None:
    if not concept_id:
        return None
    c = kb.concept(concept_id)
    return c.preferred_label_en if c else concept_id


def _fact_source(kb: KnowledgeBase, f: Any) -> str | None:
    for e in f.evidence:
        d = kb.document(e.doc_id)
        if d:
            return d.source_id
    return None
