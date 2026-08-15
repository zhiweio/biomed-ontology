"""可观测四支柱的运行时底座（L7）。

设计上刻意不依赖 OTel SDK 与 OpenSearch：本模块只定义采集契约与内存/JSONL 落盘，
真实部署时把 `TraceRecorder` 换成 OTel exporter、把 `JsonlStore` 换成 Iceberg 写入
（见 `biomed_ontology.lake` REST Catalog），接口保持 `append` / `read_all` 不变，
被埋点的业务代码一行不用改。

四支柱各自回答一个问题：
- Trace(WHERE)  这次调用经过了哪些阶段
- I/O(WHAT)     进出的具体内容是什么
- State(WHY)    每一步为什么这么选，候选里落选的是谁
- Metrics(WHEN) 指标随时间与 release 怎么变

State 是最容易被省掉、也是排障时最不可替代的一支：只记结果不记候选，
就永远回答不了"为什么没选那个"。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from biomed_ontology._generated.hmd_concept import (
    LicenseTierEnum,
    MappingJustificationEnum,
)
from biomed_ontology._generated.hmd_fact import NormalizationStageEnum

_LOG = logging.getLogger("hmd.obs")

__all__ = [
    "Candidate",
    "DecisionRecord",
    "JsonlStore",
    "MetricPoint",
    "ObservabilityHub",
    "Span",
    "ToolIoRecord",
    "TraceContext",
    "decision_subject",
    "hub_from_obs_rows",
    "new_trace_id",
    "subject_from_state",
]


def new_trace_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


# ---------------------------------------------------------------- Trace


@dataclass
class Span:
    """一个执行阶段。属性键沿用 OTel 语义约定 + 本项目的 ontology.* / hmd.* 扩展。"""

    span_id: str
    trace_id: str
    name: str
    parent_id: str | None = None
    start_ms: float = 0.0
    end_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"

    @property
    def duration_ms(self) -> float:
        return (self.end_ms or _now_ms()) - self.start_ms

    def set(self, **attrs: Any) -> None:
        self.attributes.update(attrs)


@dataclass
class TraceContext:
    """一次 tool 调用的完整上下文。trace_id 随返回体回传 agent（设计决策 D6）。"""

    trace_id: str
    ontology_release_id: str
    agent_id: str | None = None
    session_id: str | None = None
    entitlements: frozenset[str] = frozenset()
    spans: list[Span] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    _stack: list[Span] = field(default_factory=list, repr=False)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        s = Span(
            span_id=uuid.uuid4().hex[:16],
            trace_id=self.trace_id,
            name=name,
            parent_id=self._stack[-1].span_id if self._stack else None,
            start_ms=_now_ms(),
            attributes={"ontology.release_id": self.ontology_release_id, **attrs},
        )
        self.spans.append(s)
        self._stack.append(s)
        try:
            yield s
        except Exception as exc:
            s.status = "ERROR"
            s.set(**{"error.message": str(exc)})
            raise
        finally:
            s.end_ms = _now_ms()
            self._stack.pop()

    @property
    def current_span_id(self) -> str | None:
        return self._stack[-1].span_id if self._stack else None

    def record_decision(
        self,
        *,
        stage: NormalizationStageEnum | str,
        justification: MappingJustificationEnum,
        chosen: str | None,
        candidates: list[Candidate] | None = None,
        state_before: Any | None = None,
        state_after: Any | None = None,
        confidence: float | None = None,
        rule_id: str | None = None,
        model_id: str | None = None,
        elapsed_ms: float = 0.0,
    ) -> DecisionRecord:
        subject, _ = subject_from_state(state_before)
        rec = DecisionRecord(
            trace_id=self.trace_id,
            span_id=self.current_span_id,
            step_seq=len(self.decisions),
            stage=stage.value if isinstance(stage, NormalizationStageEnum) else stage,
            justification=justification,
            chosen=chosen,
            candidates=candidates or [],
            state_before=state_before,
            state_after=state_after,
            confidence=confidence,
            rule_id=rule_id,
            model_id=model_id,
            elapsed_ms=elapsed_ms,
            subject_text=subject or None,
        )
        self.decisions.append(rec)
        return rec

    def span_tree(self) -> list[str]:
        """扁平化的 span 树，供排障与测试断言使用。"""
        depth: dict[str | None, int] = {None: -1}
        out = []
        for s in self.spans:
            d = depth.get(s.parent_id, 0) + 1
            depth[s.span_id] = d
            out.append(f"{'  ' * d}{s.name}")
        return out


# ---------------------------------------------------------------- State


@dataclass
class Candidate:
    candidate_id: str
    score: float
    channel: str
    label: str | None = None
    stage: str | None = None


_SUBJECT_MAX_CHARS = 256


def subject_from_state(
    state_before: Any,
    *,
    subject_text: str | None = None,
    max_chars: int = _SUBJECT_MAX_CHARS,
) -> tuple[str, bool]:
    """WHY 主语：mention / query。按字符截，不切 JSON。"""
    if subject_text:
        raw = str(subject_text)
    elif isinstance(state_before, dict):
        raw = str(state_before.get("text") or state_before.get("query") or "")
    elif isinstance(state_before, str):
        raw = state_before
    else:
        raw = ""
    if len(raw) <= max_chars:
        return raw, False
    return raw[:max_chars], True


def decision_subject(dec: DecisionRecord) -> str:
    text, _ = subject_from_state(dec.state_before, subject_text=getattr(dec, "subject_text", None))
    return text


@dataclass
class DecisionRecord:
    trace_id: str
    step_seq: int
    stage: str
    justification: MappingJustificationEnum
    chosen: str | None
    span_id: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    state_before: Any | None = None
    state_after: Any | None = None
    confidence: float | None = None
    rule_id: str | None = None
    model_id: str | None = None
    elapsed_ms: float = 0.0
    subject_text: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["justification"] = self.justification.value
        return d


# ---------------------------------------------------------------- I/O


@dataclass
class ToolIoRecord:
    trace_id: str
    tool_name: str
    ontology_release_id: str
    input_json: str
    output_json: str
    latency_ms: float
    status: str = "OK"
    tool_version: str = "0.1.0"
    span_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    error_message: str | None = None
    contract_valid: bool = True
    contract_errors: list[str] = field(default_factory=list)
    license_filtered_count: int = 0
    caller_entitlements: list[str] = field(default_factory=list)
    max_tier_returned: LicenseTierEnum = LicenseTierEnum.TIER_0

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["max_tier_returned"] = self.max_tier_returned.value
        return d


@dataclass
class MetricPoint:
    metric_name: str
    metric_value: float
    ontology_release_id: str
    metric_dimension: str | None = None
    sample_size: int | None = None
    threshold: float | None = None
    passed: bool | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 落盘


class JsonlStore:
    """JSONL 本地 WAL。生产入湖：Kafka produce → Redpanda → Iceberg Connect Sink。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]


class ObservabilityHub:
    """四支柱的统一入口。业务代码只与它打交道，不直接接触存储。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.io_records: list[ToolIoRecord] = []
        self.decisions: list[DecisionRecord] = []
        self.spans: list[Span] = []
        self.metrics: list[MetricPoint] = []
        self._io_store = JsonlStore(root / "obs_tool_io.jsonl") if root else None
        self._dec_store = JsonlStore(root / "obs_decision.jsonl") if root else None
        self._metric_store = JsonlStore(root / "obs_metric.jsonl") if root else None
        self.emit_failures = 0

    def start_trace(
        self,
        *,
        release_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        entitlements: frozenset[str] = frozenset(),
        trace_id: str | None = None,
    ) -> TraceContext:
        return TraceContext(
            trace_id=trace_id or new_trace_id(),
            ontology_release_id=release_id,
            agent_id=agent_id,
            session_id=session_id,
            entitlements=entitlements,
        )

    def commit(self, ctx: TraceContext, io: ToolIoRecord) -> None:
        """把一次调用的全部埋点落库。调用结束时统一提交，避免部分写入。"""
        self.spans.extend(ctx.spans)
        self.decisions.extend(ctx.decisions)
        self.io_records.append(io)
        payload = io.to_json()
        if self._io_store:
            self._io_store.append(payload)
        if self._dec_store:
            for d in ctx.decisions:
                self._dec_store.append(d.to_json())
        try:
            from biomed_ontology.lake.obs_events import emit_decisions, emit_spans, emit_tool_io

            emit_tool_io(payload)
            emit_decisions(ctx.decisions, flush=False)
            emit_spans(ctx.spans)
        except Exception:
            self.emit_failures += 1
            _LOG.warning("obs emit failed trace_id=%s", io.trace_id, exc_info=True)

    def record_metric(self, point: MetricPoint) -> None:
        self.metrics.append(point)
        if self._metric_store:
            self._metric_store.append(point.to_json())

    # -------------------------------------------------- 查询（供指标与信号挖掘）

    def by_trace(
        self, trace_id: str
    ) -> tuple[list[Span], list[DecisionRecord], ToolIoRecord | None]:
        spans = [s for s in self.spans if s.trace_id == trace_id]
        decs = [d for d in self.decisions if d.trace_id == trace_id]
        io = next((r for r in self.io_records if r.trace_id == trace_id), None)
        return spans, decs, io

    def stage_hits(self) -> dict[str, int]:
        """各级联阶段的命中次数。LLM 阶段占比升高就是级联退化的早期信号。"""
        out: dict[str, int] = {}
        for d in self.decisions:
            out[d.stage] = out.get(d.stage, 0) + 1
        return out

    def latency_percentile(self, tool_name: str, pct: float = 95.0) -> float | None:
        vals = sorted(r.latency_ms for r in self.io_records if r.tool_name == tool_name)
        if not vals:
            return None
        idx = min(len(vals) - 1, round((pct / 100.0) * (len(vals) - 1)))
        return vals[idx]


def _parse_json_maybe(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _justification(value: Any) -> MappingJustificationEnum:
    raw = value.value if hasattr(value, "value") else value
    try:
        return MappingJustificationEnum(str(raw))
    except ValueError:
        return MappingJustificationEnum.UnspecifiedMatching


def _candidates_from_row(value: Any) -> list[Candidate]:
    parsed = _parse_json_maybe(value) or []
    if not isinstance(parsed, list):
        return []
    out: list[Candidate] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        out.append(
            Candidate(
                candidate_id=str(item.get("id") or item.get("candidate_id") or ""),
                score=float(item.get("score") or 0.0),
                channel=str(item.get("channel") or ""),
                label=item.get("label"),
            )
        )
    return out


def _tool_io_from_row(row: dict[str, Any]) -> ToolIoRecord:
    cv = row.get("contract_valid")
    contract_valid = cv not in (False, "false", "False", "0")
    return ToolIoRecord(
        trace_id=str(row.get("trace_id") or ""),
        tool_name=str(row.get("tool_name") or ""),
        ontology_release_id=str(row.get("ontology_release_id") or ""),
        input_json=str(row.get("input_json") or "{}"),
        output_json=str(row.get("output_json") or "{}"),
        latency_ms=float(row.get("latency_ms") or 0.0),
        status=str(row.get("status") or "OK"),
        agent_id=row.get("agent_id"),
        session_id=row.get("session_id"),
        error_message=row.get("error_message"),
        contract_valid=contract_valid,
    )


def _decision_from_row(row: dict[str, Any]) -> DecisionRecord:
    before = _parse_json_maybe(row.get("state_before"))
    subject, _ = subject_from_state(before, subject_text=row.get("subject_text"))
    return DecisionRecord(
        trace_id=str(row.get("trace_id") or ""),
        step_seq=int(row.get("step_seq") or 0),
        stage=str(row.get("stage") or ""),
        justification=_justification(row.get("justification")),
        chosen=row.get("chosen"),
        span_id=row.get("span_id"),
        candidates=_candidates_from_row(row.get("candidates_json") or row.get("candidates")),
        state_before=before,
        state_after=_parse_json_maybe(row.get("state_after")),
        confidence=row.get("confidence"),
        rule_id=row.get("rule_id"),
        model_id=row.get("model_id"),
        elapsed_ms=float(row.get("elapsed_ms") or 0.0),
        subject_text=subject or None,
    )


def hub_from_obs_rows(
    io_rows: list[dict[str, Any]] | None = None,
    decision_rows: list[dict[str, Any]] | None = None,
) -> ObservabilityHub:
    """把湖行填进临时 hub，供 miner 过夜读湖。"""
    hub = ObservabilityHub()
    for row in io_rows or []:
        hub.io_records.append(_tool_io_from_row(row))
    for row in decision_rows or []:
        hub.decisions.append(_decision_from_row(row))
    return hub
