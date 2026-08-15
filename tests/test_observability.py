"""可观测底座与契约校验。

四支柱（Trace/IO/State/Metrics）里任何一支断了，
排障就退化成"重跑一遍看看"，而 agent 场景下这条路走不通。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, MappingJustificationEnum
from biomed_ontology.observability import (
    Candidate,
    ObservabilityHub,
    ToolIoRecord,
    TraceContext,
    new_trace_id,
)
from biomed_ontology.observability.contracts import (
    ContractValidator,
    LicenseGate,
    LicenseLeak,
    citation_fidelity,
)


def test_trace_id_is_unique():
    assert len({new_trace_id() for _ in range(200)}) == 200


def test_nested_spans_form_a_tree():
    ctx = TraceContext(trace_id="t", ontology_release_id="0.1.0")
    with ctx.span("outer"):
        with ctx.span("inner"):
            pass
        with ctx.span("inner2"):
            pass
    outer = next(s for s in ctx.spans if s.name == "outer")
    inners = [s for s in ctx.spans if s.name.startswith("inner")]
    assert all(s.parent_id == outer.span_id for s in inners)
    assert outer.parent_id is None


def test_span_records_duration_and_attributes():
    ctx = TraceContext(trace_id="t", ontology_release_id="0.1.0")
    with ctx.span("work", **{"hmd.n": 3}) as sp:
        sp.set(**{"hmd.done": True})
    sp = ctx.spans[0]
    assert sp.duration_ms >= 0
    assert sp.attributes["hmd.n"] == 3
    assert sp.attributes["hmd.done"] is True


def test_decision_captures_candidates_and_state():
    """决策必须记下落选者。

    只记 chosen 的话，"为什么不是另一个"永远回答不了，
    而排障时问的恰恰是这个问题。
    """
    ctx = TraceContext(trace_id="t", ontology_release_id="0.1.0")
    with ctx.span("s"):
        ctx.record_decision(
            stage="DICTIONARY",
            justification=MappingJustificationEnum.LexicalMatching,
            chosen="HMD:ENT:DC:savolitinib",
            candidates=[
                Candidate("HMD:ENT:DC:savolitinib", 0.98, "dictionary"),
                Candidate("HMD:ENT:DC:fruquintinib", 0.41, "vector"),
            ],
            state_before={"text": "沃利替尼"},
            state_after={"concept_id": "HMD:ENT:DC:savolitinib"},
            confidence=0.98,
        )
    d = ctx.decisions[0]
    assert d.chosen == "HMD:ENT:DC:savolitinib"
    assert len(d.candidates) == 2
    assert d.state_before is not None and d.state_before["text"] == "沃利替尼"
    assert d.span_id is not None


def test_hub_commit_indexes_by_trace():
    hub = ObservabilityHub()
    ctx = hub.start_trace(release_id="0.1.0", agent_id="a")
    with ctx.span("s"):
        ctx.record_decision(
            stage="RULE",
            justification=MappingJustificationEnum.UnspecifiedMatching,
            chosen="X",
        )
    io = ToolIoRecord(
        trace_id=ctx.trace_id,
        tool_name="normalize_entity",
        ontology_release_id="0.1.0",
        input_json="{}",
        output_json="{}",
        latency_ms=1.2,
        status="OK",
    )
    hub.commit(ctx, io)
    spans, decisions, rec = hub.by_trace(ctx.trace_id)
    assert spans and decisions and rec is not None
    assert rec.tool_name == "normalize_entity"


def test_hub_commit_logs_emit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = ObservabilityHub()
    ctx = hub.start_trace(release_id="0.1.0")

    def _boom(_payload):
        raise RuntimeError("kafka down")

    monkeypatch.setattr("biomed_ontology.lake.obs_events.emit_tool_io", _boom)
    hub.commit(
        ctx,
        ToolIoRecord(
            trace_id=ctx.trace_id,
            tool_name="t",
            ontology_release_id="0.1.0",
            input_json="{}",
            output_json="{}",
            latency_ms=1.0,
            status="OK",
        ),
    )
    assert hub.emit_failures == 1
    _spans, _decs, rec = hub.by_trace(ctx.trace_id)
    assert rec is not None


def test_latency_percentile_is_monotonic():
    hub = ObservabilityHub()
    for i in range(1, 101):
        ctx = hub.start_trace(release_id="0.1.0")
        hub.commit(
            ctx,
            ToolIoRecord(
                trace_id=ctx.trace_id,
                tool_name="t",
                ontology_release_id="0.1.0",
                input_json="{}",
                output_json="{}",
                latency_ms=float(i),
                status="OK",
            ),
        )
    p50 = hub.latency_percentile("t", 50)
    p95 = hub.latency_percentile("t", 95)
    assert p50 is not None and p95 is not None
    assert p50 <= p95


# ---------------------------------------------------------------- license gate


@pytest.mark.parametrize(
    "tier,entitled,visible",
    [
        (LicenseTierEnum.TIER_0, False, True),
        (LicenseTierEnum.TIER_1, False, True),
        (LicenseTierEnum.TIER_2, False, False),
        (LicenseTierEnum.TIER_3, False, False),
        (LicenseTierEnum.TIER_3, True, True),
    ],
)
def test_visible_tier_matrix(tier, entitled, visible):
    gate = LicenseGate(frozenset({"SRC"}) if entitled else frozenset())
    assert gate.visible_tier("SRC", tier) is visible


def test_gate_reports_filtered_count_rather_than_silently_dropping():
    """过滤条数必须可见。

    悄悄少返回几条，调用方会以为"库里就这么多"；
    而这个计数正是合规审计与采购 ROI 论证的依据。
    """
    items = [("a", LicenseTierEnum.TIER_0), ("b", LicenseTierEnum.TIER_3)]
    res = LicenseGate().filter(items, tier_of=lambda i: i[1], source_of=lambda _: "SRC")
    assert len(res.kept) == 1
    assert res.filtered_count == 1
    assert res.max_tier is LicenseTierEnum.TIER_0


def test_assert_no_leak_raises():
    items = [("b", LicenseTierEnum.TIER_3)]
    with pytest.raises(LicenseLeak):
        LicenseGate().assert_no_leak(items, tier_of=lambda i: i[1], source_of=lambda _: "S")


def test_contract_validator_rejects_unknown_field():
    v = ContractValidator()
    ok = v.validate("NormalizeRequest", {"text": "MET"})
    bad = v.validate("NormalizeRequest", {"text": "MET", "nope": 1})
    assert ok.valid
    assert not bad.valid


def test_contract_validator_rejects_bad_enum():
    v = ContractValidator()
    assert not v.validate("SearchRequest", {"query": "x", "max_tier": "TIER_9"}).valid


def test_citation_fidelity_penalises_unsupported_claims():
    returned = {"D1": {"HMD:ENT:DC:savolitinib"}, "D2": {"HMD:ENT:TGT:MET"}}
    assert citation_fidelity([("D1", None), ("D2", None)], returned) == 1.0
    # 引用了不在返回集里的文档
    assert citation_fidelity([("D1", None), ("D9", None)], returned) == 0.5
    assert citation_fidelity([], returned) == 1.0


def test_citation_fidelity_catches_right_doc_wrong_concept():
    """引用了正确文档但归因到错误概念 —— 这是最难人工发现的一类错误。"""
    returned = {"D1": {"HMD:ENT:DC:savolitinib"}}
    assert citation_fidelity([("D1", "HMD:ENT:DC:savolitinib")], returned) == 1.0
    assert citation_fidelity([("D1", "HMD:ENT:TGT:MET")], returned) == 0.0
