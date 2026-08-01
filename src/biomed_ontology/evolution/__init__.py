"""L8 演进闭环：使用信号 → 候选变更 → KGCL changeset → 发版。

核心主张：本体的缺口应当由真实使用暴露，而不是靠人工巡检去猜。
因此 8 个 miner 的输入全部来自 P6 落下的 trace / decision / IO / feedback，
不引入任何离线人工标注作为触发源。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from biomed_ontology._generated.hmd_obs import SignalStatusEnum, SignalTypeEnum

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.observability import ObservabilityHub
    from biomed_ontology.pipeline import KnowledgeBase

__all__ = [
    "ChangeSet",
    "KgclOp",
    "MiningInput",
    "ReleasePlan",
    "Signal",
    "SignalMiner",
    "build_changeset",
    "generate_candidates",
    "mine_signals",
    "plan_release",
]

# 信号定级阈值。数字放在模块顶层而不是散在函数里，是为了让"为什么这条被判 P1"
# 可以直接对照常量回答，而不用去读调用栈。
LOW_CONFIDENCE_THRESHOLD = 0.60
MIN_OCCURRENCES = 2
HIGH_PRIORITY_OCCURRENCES = 5
COOCCURRENCE_MIN_DOCS = 2


@dataclass
class Signal:
    signal_id: str
    signal_type: SignalTypeEnum
    payload: str
    occurrences: int = 1
    first_seen_trace: str | None = None
    example_traces: list[str] = field(default_factory=list)
    status: SignalStatusEnum = SignalStatusEnum.NEW
    priority: str = "P2"
    detected_in_release: str = "0.1.0"
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type.value,
            "payload": self.payload,
            "occurrences": self.occurrences,
            "first_seen_trace": self.first_seen_trace,
            "example_traces": self.example_traces[:5],
            "signal_status": self.status.value,
            "priority": self.priority,
            "detected_in_release": self.detected_in_release,
            "evidence": self.evidence,
        }


def _signal_id(signal_type: SignalTypeEnum, payload: str) -> str:
    """信号 ID 由类型+载荷派生而非自增。

    同一个缺口在多次挖掘中必须落到同一个 ID，否则每跑一次都变成"新信号"，
    审校队列会被重复项淹没，状态流转（TRIAGED/DISMISSED）也全部失效。
    """
    h = hashlib.sha1(f"{signal_type.value}|{payload}".encode()).hexdigest()[:9]
    return f"HMDS:{int(h, 16) % 1_000_000_000:09d}"


def _priority(occurrences: int, *, base: str = "P2") -> str:
    if occurrences >= HIGH_PRIORITY_OCCURRENCES:
        return "P0"
    if occurrences >= MIN_OCCURRENCES:
        return "P1"
    return base


@dataclass
class MiningInput:
    """挖掘输入。把它显式建模，是为了让 miner 可以脱离运行时单测。"""

    kb: KnowledgeBase
    hub: ObservabilityHub
    feedback: list[Any] = field(default_factory=list)
    queries: list[tuple[str, int]] = field(default_factory=list)
    clicks: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_runtime(cls, kb: KnowledgeBase, api: Any) -> MiningInput:
        return cls(
            kb=kb,
            hub=api.hub,
            feedback=list(getattr(api, "feedback_log", [])),
            queries=list(getattr(api, "query_log", [])),
            clicks=list(getattr(api, "click_log", [])),
        )


SignalMiner = Any  # Callable[[MiningInput], list[Signal]]


# ------------------------------------------------------------------ 8 个 miner


def _mine_unmapped_span(mi: MiningInput) -> list[Signal]:
    counter: Counter[str] = Counter()
    traces: dict[str, list[str]] = defaultdict(list)
    for rec in mi.hub.io_records:
        out = _loads(rec.output_json)
        for span in out.get("unmapped_spans") or []:
            counter[span] += 1
            traces[span].append(rec.trace_id)
    return [
        Signal(
            _signal_id(SignalTypeEnum.unmapped_span, text),
            SignalTypeEnum.unmapped_span,
            text,
            n,
            traces[text][0],
            traces[text],
            priority=_priority(n),
            detected_in_release=mi.kb.release_id,
        )
        for text, n in counter.most_common()
    ]


def _mine_low_confidence(mi: MiningInput) -> list[Signal]:
    worst: dict[str, tuple[float, str, str]] = {}
    counter: Counter[str] = Counter()
    for rec in mi.hub.io_records:
        out = _loads(rec.output_json)
        for m in out.get("matched_concepts") or []:
            conf = m.get("confidence")
            if conf is None or conf >= LOW_CONFIDENCE_THRESHOLD:
                continue
            key = f"{m.get('matched_text')}→{m.get('concept_id')}"
            counter[key] += 1
            if key not in worst or conf < worst[key][0]:
                worst[key] = (conf, rec.trace_id, m.get("stage") or "")
    out_signals = []
    for key, n in counter.most_common():
        conf, trace, stage = worst[key]
        out_signals.append(
            Signal(
                _signal_id(SignalTypeEnum.low_confidence_normalization, key),
                SignalTypeEnum.low_confidence_normalization,
                key,
                n,
                trace,
                [trace],
                priority=_priority(n),
                detected_in_release=mi.kb.release_id,
                evidence={"min_confidence": round(conf, 4), "stage": stage},
            )
        )
    return out_signals


def _mine_ambiguous_unstable(mi: MiningInput) -> list[Signal]:
    """同一别名在不同上下文落到不同概念 —— 且至少出现过一次弃权。

    只看"落到不同概念"会把正常的上下文消歧误判成不稳定；
    真正值得人工介入的是系统自己都拿不准（ABSTAIN）的那一批。
    """
    by_text: dict[str, set[str]] = defaultdict(set)
    abstained: dict[str, int] = Counter()
    traces: dict[str, list[str]] = defaultdict(list)
    for dec in mi.hub.decisions:
        if dec.stage not in {"LLM_DISAMBIGUATION", "llm_disambiguation"}:
            continue
        text = (dec.state_before or {}).get("text") or dec.chosen or ""
        if not text:
            continue
        traces[text].append(dec.trace_id)
        if dec.chosen:
            by_text[text].add(dec.chosen)
        else:
            abstained[text] += 1
    return [
        Signal(
            _signal_id(SignalTypeEnum.ambiguous_unstable, text),
            SignalTypeEnum.ambiguous_unstable,
            text,
            len(traces[text]),
            traces[text][0],
            traces[text],
            priority=_priority(len(traces[text]), base="P1"),
            detected_in_release=mi.kb.release_id,
            evidence={"distinct_concepts": sorted(senses), "abstains": abstained.get(text, 0)},
        )
        for text, senses in by_text.items()
        if len(senses) > 1 or abstained.get(text, 0) > 0
    ] + [
        Signal(
            _signal_id(SignalTypeEnum.ambiguous_unstable, text),
            SignalTypeEnum.ambiguous_unstable,
            text,
            n,
            traces[text][0],
            traces[text],
            priority=_priority(n, base="P1"),
            detected_in_release=mi.kb.release_id,
            evidence={"distinct_concepts": [], "abstains": n},
        )
        for text, n in abstained.items()
        if text not in by_text
    ]


def _mine_zero_result(mi: MiningInput) -> list[Signal]:
    counter: Counter[str] = Counter()
    traces: dict[str, list[str]] = defaultdict(list)
    for rec in mi.hub.io_records:
        if rec.tool_name != "search_documents":
            continue
        out = _loads(rec.output_json)
        if out.get("total"):
            continue
        q = _loads(rec.input_json).get("query") or ""
        if not q:
            continue
        counter[q] += 1
        traces[q].append(rec.trace_id)
    for q, n in mi.queries:
        if n == 0:
            counter[q] += 1
    return [
        Signal(
            _signal_id(SignalTypeEnum.zero_result_query, q),
            SignalTypeEnum.zero_result_query,
            q,
            n,
            (traces[q] or [None])[0],
            traces[q],
            priority=_priority(n, base="P1"),
            detected_in_release=mi.kb.release_id,
        )
        for q, n in counter.most_common()
    ]


def _mine_expansion_miss(mi: MiningInput) -> list[Signal]:
    """用户点了的文档没进扩展召回 —— 说明扩展词集漏了一层。"""
    returned: dict[str, set[str]] = defaultdict(set)
    for rec in mi.hub.io_records:
        if rec.tool_name != "search_documents":
            continue
        q = _loads(rec.input_json).get("query") or ""
        for hit in _loads(rec.output_json).get("results") or []:
            returned[q].add(hit.get("chunk_id", ""))
    signals = []
    for q, chunk_id in mi.clicks:
        if chunk_id in returned.get(q, set()):
            continue
        payload = f"{q}→{chunk_id}"
        signals.append(
            Signal(
                _signal_id(SignalTypeEnum.expansion_miss, payload),
                SignalTypeEnum.expansion_miss,
                payload,
                1,
                None,
                [],
                priority="P1",
                detected_in_release=mi.kb.release_id,
                evidence={"query": q, "missed_chunk": chunk_id},
            )
        )
    return signals


def _mine_negative_feedback(mi: MiningInput) -> list[Signal]:
    signals = []
    for fb in mi.feedback:
        verdict = getattr(fb, "verdict", None)
        if verdict in {"CORRECT", None}:
            continue
        subject = getattr(fb, "subject_id", None)
        expected = getattr(fb, "expected_id", None)
        comment = getattr(fb, "comment", None)
        payload = f"{verdict}:{subject or getattr(fb, 'query', None) or comment or ''}"
        signals.append(
            Signal(
                _signal_id(SignalTypeEnum.negative_feedback, payload),
                SignalTypeEnum.negative_feedback,
                payload,
                1,
                getattr(fb, "trace_id", None),
                [getattr(fb, "trace_id", "") or ""],
                # 负反馈直接进 P0：它是唯一一种"人已经明确说错了"的信号，
                # 让它排在统计类信号后面等于把最强的证据压在最下面。
                priority="P0",
                detected_in_release=mi.kb.release_id,
                evidence={
                    "verdict": verdict,
                    "expected_concept_id": expected,
                    "offending_concept_id": subject,
                    "free_text": comment,
                },
            )
        )
    return signals


def _mine_cooccurrence_anomaly(mi: MiningInput) -> list[Signal]:
    """两个概念在多篇文档同现却没有任何事实边 —— 提示缺失关系。"""
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ch in mi.kb.chunks:
        ids = sorted(set(ch.concept_ids))
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pairs[(a, b)].add(ch.doc_id)
    linked = {tuple(sorted((f.subject_id, f.object_id))) for f in mi.kb.facts if f.object_id}
    signals = []
    for (a, b), docs in pairs.items():
        if len(docs) < COOCCURRENCE_MIN_DOCS or (a, b) in linked:
            continue
        payload = f"{a}~{b}"
        signals.append(
            Signal(
                _signal_id(SignalTypeEnum.cooccurrence_anomaly, payload),
                SignalTypeEnum.cooccurrence_anomaly,
                payload,
                len(docs),
                None,
                [],
                priority=_priority(len(docs)),
                detected_in_release=mi.kb.release_id,
                evidence={
                    "docs": sorted(docs),
                    "labels": [_label(mi.kb, a), _label(mi.kb, b)],
                },
            )
        )
    return sorted(signals, key=lambda s: (-s.occurrences, s.payload))


def _mine_multi_source_conflict(mi: MiningInput) -> list[Signal]:
    """同一 (主语, 谓词, 指标) 在不同源给出不同数值。

    Track B 商业源接入后这是主力信号；现在先把管道打通，
    否则采购到货那天才发现没地方落，是最贵的一种返工。
    """
    groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for f in mi.kb.facts:
        if f.object_value is None:
            continue
        groups[(f.subject_id, f.predicate.value, _qualifier(f, "metric"))].append(f)
    signals = []
    for (subj, pred, metric), facts in groups.items():
        values = {}
        for f in facts:
            try:
                values[float(f.object_value)] = f
            except (TypeError, ValueError):
                continue
        if len(values) < 2:
            continue
        lo, hi = min(values), max(values)
        if hi == 0 or abs(hi - lo) / abs(hi) < 0.10:
            continue
        payload = f"{subj}|{pred}|{metric}"
        signals.append(
            Signal(
                _signal_id(SignalTypeEnum.multi_source_conflict, payload),
                SignalTypeEnum.multi_source_conflict,
                payload,
                len(values),
                None,
                [],
                priority="P0",
                detected_in_release=mi.kb.release_id,
                evidence={
                    "values": sorted(values),
                    "sources": sorted({e.doc_id for f in facts for e in f.evidence}),
                },
            )
        )
    return signals


MINERS: dict[SignalTypeEnum, Any] = {
    SignalTypeEnum.unmapped_span: _mine_unmapped_span,
    SignalTypeEnum.low_confidence_normalization: _mine_low_confidence,
    SignalTypeEnum.ambiguous_unstable: _mine_ambiguous_unstable,
    SignalTypeEnum.zero_result_query: _mine_zero_result,
    SignalTypeEnum.expansion_miss: _mine_expansion_miss,
    SignalTypeEnum.negative_feedback: _mine_negative_feedback,
    SignalTypeEnum.cooccurrence_anomaly: _mine_cooccurrence_anomaly,
    SignalTypeEnum.multi_source_conflict: _mine_multi_source_conflict,
}


def mine_signals(mi: MiningInput, *, types: list[SignalTypeEnum] | None = None) -> list[Signal]:
    out: list[Signal] = []
    for st in types or list(MINERS):
        out.extend(MINERS[st](mi))
    order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(out, key=lambda s: (order.get(s.priority, 9), -s.occurrences, s.payload))


# ------------------------------------------------------------------ KGCL 变更


@dataclass
class KgclOp:
    """一条 KGCL 操作。用 KGCL 而不是自定义 diff 格式，
    是因为它是本体演进的既有标准，采购 UMLS/MedDRA 后可直接对接上游变更流。"""

    op: str
    about: str
    old_value: str | None = None
    new_value: str | None = None
    qualifier: str | None = None
    signal_id: str | None = None
    rationale: str = ""

    def to_kgcl(self) -> str:
        if self.op == "create synonym":
            return f"create {self.qualifier or 'exact'} synonym '{self.new_value}' for {self.about}"
        if self.op == "obsolete":
            return f"obsolete {self.about}"
        if self.op == "create edge":
            return f"create edge {self.about} {self.qualifier} {self.new_value}"
        if self.op == "change definition":
            return (
                f"change definition of {self.about} from '{self.old_value}' to '{self.new_value}'"
            )
        if self.op == "create node":
            return f"create node {self.about} '{self.new_value}'"
        return f"# unsupported op {self.op} on {self.about}"


@dataclass
class ChangeSet:
    release_id: str
    base_release_id: str
    ops: list[KgclOp] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_kgcl(self) -> str:
        lines = [
            f"# changeset {self.base_release_id} -> {self.release_id}",
            f"# generated {self.created_at}",
        ]
        for op in self.ops:
            if op.rationale:
                lines.append(f"# [{op.signal_id}] {op.rationale}")
            lines.append(op.to_kgcl())
        return "\n".join(lines) + "\n"

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "base_release_id": self.base_release_id,
            "created_at": self.created_at,
            "ops": [
                {
                    "op": o.op,
                    "about": o.about,
                    "old_value": o.old_value,
                    "new_value": o.new_value,
                    "qualifier": o.qualifier,
                    "signal_id": o.signal_id,
                    "rationale": o.rationale,
                }
                for o in self.ops
            ],
            "signals": [s.as_dict() for s in self.signals],
        }


_CODE_LIKE = re.compile(r"^[A-Z][A-Za-z]{0,7}[-‐]?\d{2,6}$")


def generate_candidates(kb: KnowledgeBase, signals: list[Signal]) -> list[KgclOp]:
    """信号 → 候选变更。

    刻意只生成"低风险且可自动判定"的变更：新增别名、补关系边。
    合并/废弃概念一律不自动生成 —— 那类操作错了就会静默污染全部下游召回，
    必须留给人工审校。
    """
    ops: list[KgclOp] = []
    for sig in signals:
        if sig.signal_type is SignalTypeEnum.unmapped_span:
            target = _guess_concept(kb, sig)
            if target and _CODE_LIKE.match(sig.payload):
                ops.append(
                    KgclOp(
                        "create synonym",
                        target,
                        new_value=sig.payload,
                        qualifier="exact",
                        signal_id=sig.signal_id,
                        rationale=(
                            f"研发代号形态、{sig.occurrences} 次未命中，同文档已命中 {target}"
                        ),
                    )
                )
            else:
                ops.append(
                    KgclOp(
                        "create node",
                        f"NEW:{sig.signal_id}",
                        new_value=sig.payload,
                        signal_id=sig.signal_id,
                        rationale=f"{sig.occurrences} 次未命中，无可挂靠概念，待人工确认实体类型",
                    )
                )
        elif sig.signal_type is SignalTypeEnum.cooccurrence_anomaly:
            a, _, b = sig.payload.partition("~")
            ops.append(
                KgclOp(
                    "create edge",
                    a,
                    qualifier="hmd:related_to",
                    new_value=b,
                    signal_id=sig.signal_id,
                    rationale=f"在 {sig.occurrences} 篇文档同现但无事实边",
                )
            )
        elif sig.signal_type is SignalTypeEnum.negative_feedback:
            expected = (sig.evidence or {}).get("expected_concept_id")
            if expected:
                ops.append(
                    KgclOp(
                        "create synonym",
                        expected,
                        new_value=(sig.evidence or {}).get("free_text") or sig.payload,
                        qualifier="exact",
                        signal_id=sig.signal_id,
                        rationale="人工负反馈直接指明了期望概念",
                    )
                )
    return ops


def _guess_concept(kb: KnowledgeBase, sig: Signal) -> str | None:
    """未命中片段的挂靠猜测：取同 chunk 内出现最多的概念。

    这是启发式，所以产出的一定是"候选"而非"变更"——
    自动落库的话，一次猜错就会把噪声写成事实。
    """
    hits: Counter[str] = Counter()
    for ch in kb.chunks:
        if sig.payload in ch.text:
            hits.update(ch.concept_ids)
    return hits.most_common(1)[0][0] if hits else None


def build_changeset(
    kb: KnowledgeBase,
    signals: list[Signal],
    *,
    release_id: str,
    approved_signal_ids: set[str] | None = None,
) -> ChangeSet:
    selected = [
        s for s in signals if approved_signal_ids is None or s.signal_id in approved_signal_ids
    ]
    for s in selected:
        s.status = SignalStatusEnum.CANDIDATE_GENERATED
    return ChangeSet(
        release_id=release_id,
        base_release_id=kb.release_id,
        ops=generate_candidates(kb, selected),
        signals=selected,
    )


# ------------------------------------------------------------------ 发版


@dataclass
class ReleasePlan:
    release_id: str
    base_release_id: str
    changeset: ChangeSet
    quality_passed: bool
    quality_blocking: list[str]
    impact: dict[str, Any]
    approved: bool

    def explain(self) -> str:
        head = f"release {self.base_release_id} → {self.release_id}"
        verdict = "可发版" if self.approved else "阻断"
        lines = [
            f"{head}：{verdict}",
            f"  变更 {len(self.changeset.ops)} 条 / 信号 {len(self.changeset.signals)} 条",
            f"  质量门 {'通过' if self.quality_passed else '未通过'}",
        ]
        lines += [f"    - {b}" for b in self.quality_blocking]
        lines.append(
            f"  影响面：概念 {self.impact['concepts_touched']} / "
            f"预计受影响 chunk {self.impact['chunks_affected']} / "
            f"需重建索引 {'是' if self.impact['reindex_required'] else '否'}"
        )
        return "\n".join(lines)


def analyze_impact(kb: KnowledgeBase, cs: ChangeSet) -> dict[str, Any]:
    """发版前的影响面分析。没有它，"改一条别名"和"改一条根概念"看起来一样安全。"""
    touched = {op.about for op in cs.ops if op.about.startswith("HMD:")}
    expanded = set(touched)
    for cid in touched:
        expanded.update(kb.normalizer.descendants(cid, 3))
    affected = [ch for ch in kb.chunks if expanded & set(ch.concept_ids_expanded or ch.concept_ids)]
    return {
        "concepts_touched": len(touched),
        "concepts_with_descendants": len(expanded),
        "chunks_affected": len(affected),
        "docs_affected": len({c.doc_id for c in affected}),
        "new_nodes": sum(1 for op in cs.ops if op.op == "create node"),
        # 只有拓扑变化才需要重建图索引；纯加别名走增量即可。
        "reindex_required": any(
            op.op in {"create edge", "create node", "obsolete"} for op in cs.ops
        ),
    }


def plan_release(
    kb: KnowledgeBase,
    cs: ChangeSet,
    *,
    gate_result: Any,
    require_human_approval: bool = True,
    approved_by: str | None = None,
) -> ReleasePlan:
    """双闸门：质量门 + 人工审批。

    两道都设，是因为它们挡的是不同东西 —— 质量门挡"数据坏了"，
    人工审批挡"数据没坏但语义错了"，后者机器判不出来。
    """
    impact = analyze_impact(kb, cs)
    blocking = [v.rule for v in gate_result.report.errors()] if not gate_result.passed else []
    human_ok = (not require_human_approval) or bool(approved_by)
    if not human_ok:
        blocking = [*blocking, "human_approval_missing"]
    approved = gate_result.passed and human_ok
    if approved:
        for s in cs.signals:
            s.status = SignalStatusEnum.RELEASED
    return ReleasePlan(
        release_id=cs.release_id,
        base_release_id=cs.base_release_id,
        changeset=cs,
        quality_passed=gate_result.passed,
        quality_blocking=blocking,
        impact=impact,
        approved=approved,
    )


def write_release_artifacts(plan: ReleasePlan, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    kgcl = out_dir / f"{plan.release_id}.kgcl"
    kgcl.write_text(plan.changeset.to_kgcl(), encoding="utf-8")
    meta = out_dir / f"{plan.release_id}.json"
    meta.write_text(
        json.dumps(
            {
                "release_id": plan.release_id,
                "base_release_id": plan.base_release_id,
                "approved": plan.approved,
                "quality_passed": plan.quality_passed,
                "quality_blocking": plan.quality_blocking,
                "impact": plan.impact,
                "changeset": plan.changeset.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return [kgcl, meta]


# ------------------------------------------------------------------ 辅助


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return v if isinstance(v, dict) else {}


def _label(kb: KnowledgeBase, cid: str) -> str:
    c = kb.concept(cid)
    return (c.preferred_label_en or c.preferred_label_zh or cid) if c else cid


def _qualifier(fact: Any, key: str) -> str:
    """qualifiers 存成 `k=v` 字符串列表（便于 RDF 平铺），这里取回单个键。"""
    prefix = f"{key}="
    for q in fact.qualifiers or []:
        if q.startswith(prefix):
            return q[len(prefix) :]
    return ""
