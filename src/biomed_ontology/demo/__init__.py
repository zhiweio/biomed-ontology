"""6 个演示场景。

每个场景都直接调 agent tool 而不是内部函数 —— 演示的是"agent 能拿到什么"，
用内部函数演示会得出一个 agent 实际上够不着的结论。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from biomed_ontology.agentapi import AgentApi
from biomed_ontology.evolution import (
    MiningInput,
    build_changeset,
    mine_signals,
    plan_release,
)
from biomed_ontology.pipeline import KnowledgeBase
from biomed_ontology.quality import QualityGate

__all__ = ["DEMOS", "DemoResult", "run_all", "run_demo"]

_LICENSED = frozenset({"MOCK_LICENSED"})


@dataclass
class DemoResult:
    demo_id: str
    title: str
    claim: str
    lines: list[str] = field(default_factory=list)
    passed: bool = True

    def render(self) -> str:
        mark = "✓" if self.passed else "✗"
        out = [f"{mark} [{self.demo_id}] {self.title}", f"   论点：{self.claim}"]
        out += [f"   {ln}" for ln in self.lines]
        return "\n".join(out)


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


# ---------------------------------------------------------------- D1 别名一致性


def demo_alias_consistency(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult(
        "D1",
        "别名一致性",
        "同一实体的 6 种写法必须落到同一个 code，且概念级召回完全一致",
    )
    variants = ["savolitinib", "沃利替尼", "AZD6094", "HMPL-504", "沃瑞沙", "AZD-6094"]
    target = "HMD:SUB:0000001"
    ids = {}
    for v in variants:
        res = api.normalize_entity(v, entity_types=["SUBSTANCE"])
        ids[v] = (res["matched_concepts"] or [{}])[0].get("concept_id")
    unique = set(ids.values())
    r.lines.append(f"归一化落到 {len(unique)} 个 code：{unique}")

    # 判定取"接地到目标概念的片段"而不是全部命中：
    # 字符 3-gram 通道会让 "savolitinib" 泄到其它 -tinib 药物、
    # "沃利替尼" 泄到其它"替尼"，那是字面噪声，不是本体层要证明的东西。
    grounded, raw = {}, {}
    for v in variants:
        hits = api.search_documents(v, top_k=10)["results"]
        raw[v] = {h["chunk_id"] for h in hits}
        grounded[v] = {h["chunk_id"] for h in hits if target in (h.get("concept_ids") or [])}
    base_g, base_r = grounded[variants[0]], raw[variants[0]]
    jac_g = {v: round(_jaccard(base_g, s), 3) for v, s in grounded.items()}
    jac_r = {v: round(_jaccard(base_r, s), 3) for v, s in raw.items()}
    r.lines.append(f"概念级召回 Jaccard：{jac_g}")
    r.lines.append(f"字面召回 Jaccard（含 3-gram 噪声，仅供对照）：{jac_r}")
    r.passed = len(unique) == 1 and None not in unique and min(jac_g.values()) == 1.0
    return r


# ---------------------------------------------------------------- D2 层级扩展


def demo_hierarchy_expansion(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult(
        "D2",
        "层级扩展",
        "查『肺癌』应召回只提『肺腺癌 / NSCLC』的文档，纯关键词做不到",
    )
    query = "肺癌"
    exp = api.expand_concept("HMD:DIS:0000003", max_depth=2)
    terms = [t["term"] for t in exp["expansion_terms"]]
    r.lines.append(f"肺癌扩展 {len(terms)} 个词：{terms[:12]}")

    hits = api.search_documents(query, top_k=10, expand=True)["results"]
    # 真正的证据是"正文里根本没出现过查询词却被召回"；
    # 比"开/关扩展的条数差"可靠，后者在小语料上会被 top_k 上限掩盖掉。
    literal_free = [
        h
        for h in hits
        if query not in (kb.chunk(h["chunk_id"]).text if kb.chunk(h["chunk_id"]) else "")
    ]
    r.lines.append(f"召回 {len(hits)} 条，其中 {len(literal_free)} 条正文未出现过「{query}」：")
    for h in literal_free[:5]:
        r.lines.append(f"  + {h['doc_id']}#{h.get('section')} :: {h['snippet'][:52]}")
        r.lines.append(f"      接地概念 {h.get('concept_ids')}")
    r.passed = bool(literal_free) and any("NSCLC" in t or "肺腺癌" in t for t in terms)
    return r


# ---------------------------------------------------------------- D3 跨语言


def demo_cross_lingual(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult("D3", "跨语言", "中文查询召回英文文献，中英证据合并到同一条事实上")
    hits = api.search_documents("沃利替尼 非小细胞肺癌", top_k=5)["results"]
    langs = {kb.document(h["doc_id"]).language.value for h in hits}
    r.lines.append(f"中文 query 命中语种：{sorted(langs)}")
    for h in hits[:4]:
        r.lines.append(f"  {h['doc_id']}#{h.get('section')} :: {h['snippet'][:52]}")

    facts = api.get_facts(subject_id="HMD:SUB:0000001", predicate="inhibits")["facts"]
    for f in facts:
        docs = {e["doc_id"] for e in f["evidence"]}
        if len(docs) > 1:
            r.lines.append(f"事实 {f['fact_id']} 证据来自 {len(docs)} 篇：{sorted(docs)}")
            for e in f["evidence"]:
                r.lines.append(f"    “{e['quote'][:46]}”")
    merged = any(len({e["doc_id"] for e in f["evidence"]}) > 1 for f in facts)
    r.passed = len(langs) > 1 and merged
    return r


# ---------------------------------------------------------------- D4 归因排障


def demo_traceability(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult(
        "D4",
        "归因排障",
        "一次 MET 消歧要能回答：哪个决策点、看了什么线索、为何弃权",
    )
    res = api.normalize_entity(
        "8 MET of moderate exercise", context="运动 代谢当量 metabolic equivalent of task"
    )
    trace_id = res["trace_id"]
    spans, decisions, io = api.hub.by_trace(trace_id)
    r.lines.append(f"trace {trace_id}  span {len(spans)} 个 / decision {len(decisions)} 条")
    for s in spans:
        r.lines.append(f"  span {s.name} {s.duration_ms:.2f}ms {dict(s.attributes)}")
    for d in decisions:
        r.lines.append(
            f"  decision[{d.stage}] chosen={d.chosen} conf={d.confidence} "
            f"model={d.model_id} 依据={d.justification}"
        )
        for c in d.candidates or []:
            r.lines.append(f"     候选 {c.candidate_id} score={c.score:.4f} ch={c.channel}")
    if io:
        r.lines.append(
            f"  IO 契约合法={io.contract_valid} 延迟={io.latency_ms:.2f}ms "
            f"license过滤={io.license_filtered_count}"
        )
    r.passed = bool(decisions) and not res["matched_concepts"]
    return r


# ---------------------------------------------------------------- D5 演进闭环


def demo_evolution_loop(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult("D5", "演进闭环", "一次未命中 → 信号 → KGCL → 双闸门发版，全程留痕")
    api.normalize_entity("Zanubrutinib 联合 ABT-869 治疗", detect_spans=True)
    api.submit_feedback(
        "MISSING_ALIAS",
        source_trace_id="demo",
        expected_concept_id="HMD:SUB:0000001",
        free_text="奥希替尼耐药后换用沃利替尼",
    )
    sigs = mine_signals(MiningInput.from_runtime(kb, api))
    r.lines.append(f"挖出信号 {len(sigs)} 条")
    for s in sigs[:5]:
        r.lines.append(
            f"  [{s.priority}] {s.signal_type.value} {s.payload[:40]!r} x{s.occurrences}"
        )
    cs = build_changeset(kb, sigs, release_id="0.2.0")
    r.lines.append(f"生成 KGCL {len(cs.ops)} 条：")
    r.lines += [f"    {op.to_kgcl()}" for op in cs.ops[:5]]
    gate = QualityGate().evaluate(
        kb, manual_accuracy={"SUBSTANCE": 0.96, "TARGET": 0.94, "DISEASE": 0.93}
    )
    blocked = plan_release(kb, cs, gate_result=gate)
    approved = plan_release(kb, cs, gate_result=gate, approved_by="curator@asliva")
    r.lines.append(
        f"无人工审批 → {'可发版' if blocked.approved else '阻断'}（{blocked.quality_blocking}）"
    )
    r.lines.append(f"有人工审批 → {'可发版' if approved.approved else '阻断'}")
    r.lines.append(f"影响面 {approved.impact}")
    r.passed = bool(sigs) and bool(cs.ops) and not blocked.approved and approved.approved
    return r


# ---------------------------------------------------------------- D6 事实溯源 + license


def demo_facts_and_license(kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    r = DemoResult(
        "D6",
        "结构化事实溯源 + 许可隔离",
        "每条事实都能定位到页/区块；无凭据时商业源内容必须完全不可见",
    )
    facts = api.get_facts(subject_id="HMD:SUB:0000002")["facts"]
    for f in facts[:4]:
        e = f["evidence"][0]
        r.lines.append(
            f"  {f['subject_label']} -{f['predicate']}-> "
            f"{f['object_label'] or f['object_value']}{f.get('object_unit') or ''} "
            f"[{f['modality']}] ← {e['doc_id']} p{e.get('page')} {e.get('section')}"
        )

    free = api.get_facts(subject_id="HMD:SUB:0000001")
    paid = api.get_facts(subject_id="HMD:SUB:0000001", entitlements=_LICENSED)
    r.lines.append(
        f"事实：无凭据 {free['total']} 条（过滤 {free['license_filtered_count']}，"
        f"最高 {free['license_tier_max']}） / 有凭据 {paid['total']} 条"
        f"（最高 {paid['license_tier_max']}）"
    )

    q = "acquired resistance competitive landscape after savolitinib"
    s_free = api.search_documents(q, top_k=3)
    s_paid = api.search_documents(q, top_k=3, entitlements=_LICENSED)
    leaked = [h for h in s_free["results"] if h["doc_id"].startswith("DOC:PATSNAP")]
    unlocked = [h for h in s_paid["results"] if h["doc_id"].startswith("DOC:PATSNAP")]
    r.lines.append(
        f"检索：无凭据命中商业源 {len(leaked)} 条（过滤 {s_free['license_filtered_count']}） / "
        f"有凭据命中 {len(unlocked)} 条"
    )

    # graph_inventory 查的是元数据图，两边都看得到——这是故意的：
    # "存在你读不了的源"本身不是机密，藏掉它会让采购决策失去依据。
    inv = api.sparql_query("graph_inventory")
    r.lines.append(f"SPARQL 元数据：可见命名图 {inv['total']} 个（含不可读的）")
    tri_free = sum(kb.graph.count_triples(g) for g in kb.graph.visible_graphs(frozenset()))
    tri_paid = sum(kb.graph.count_triples(g) for g in kb.graph.visible_graphs(_LICENSED))
    r.lines.append(f"可读三元组：无凭据 {tri_free} / 有凭据 {tri_paid}")
    r.passed = (
        not leaked
        and bool(unlocked)
        and paid["total"] > free["total"]
        and free["license_filtered_count"] > 0
        and tri_paid > tri_free
    )
    return r


DEMOS: dict[str, Callable[[KnowledgeBase, AgentApi], DemoResult]] = {
    "D1": demo_alias_consistency,
    "D2": demo_hierarchy_expansion,
    "D3": demo_cross_lingual,
    "D4": demo_traceability,
    "D5": demo_evolution_loop,
    "D6": demo_facts_and_license,
}


def run_demo(demo_id: str, kb: KnowledgeBase, api: AgentApi) -> DemoResult:
    return DEMOS[demo_id](kb, api)


def run_all(kb: KnowledgeBase, api: AgentApi | None = None) -> list[DemoResult]:
    api = api or AgentApi.from_kb(kb)
    # 每个场景一个 api 实例，但共用 kb.hub —— 于是 D4 那次弃权会作为 unmapped
    # 信号出现在 D5 的挖掘结果里。这不是串扰，正是要演示的闭环本身。
    return [DEMOS[d](kb, AgentApi.from_kb(kb)) for d in DEMOS]


def summary_json(results: list[DemoResult]) -> str:
    return json.dumps(
        [
            {"demo_id": r.demo_id, "title": r.title, "passed": r.passed, "lines": r.lines}
            for r in results
        ],
        ensure_ascii=False,
        indent=2,
    )
