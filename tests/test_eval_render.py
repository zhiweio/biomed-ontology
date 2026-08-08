"""eval Rich 渲染：对齐 foundation golden / demo 的 CLI 面，不能退回纯 print。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from biomed_ontology.eval import ArmResult, NormalizationEval, RetrievalEval
from biomed_ontology.eval.render import render_eval, summary_json
from biomed_ontology.eval.targets import MetricTarget, TargetOutcome


def _arm(name: str, label: str, **kwargs: float) -> ArmResult:
    return ArmResult(
        arm=name,
        label=label,
        recall_at_10=kwargs.get("recall_at_10", 0.4),
        precision_at_5=kwargs.get("precision_at_5", 0.3),
        ndcg_at_10=kwargs.get("ndcg_at_10", 0.35),
        mrr=kwargs.get("mrr", 0.5),
        map_score=kwargs.get("map_score", 0.4),
        citation_fidelity=kwargs.get("citation_fidelity", 1.0),
        query_count=int(kwargs.get("query_count", 10)),
        latency_p50_ms=kwargs.get("latency_p50_ms", 12.0),
        per_query={
            "q1": {"recall": 0.5, "precision": 0.4, "ndcg": 0.4, "rr": 0.5, "ap": 0.4},
            "q2": {"recall": 0.3, "precision": 0.2, "ndcg": 0.3, "rr": 0.5, "ap": 0.4},
        },
    )


def _sample() -> tuple[NormalizationEval, RetrievalEval, list[TargetOutcome]]:
    base = _arm("bm25_only", "纯 BM25（无本体）", ndcg_at_10=0.30, recall_at_10=0.35)
    tgt = _arm("ontology_hybrid", "本体增强混合", ndcg_at_10=0.40, recall_at_10=0.45)
    # 给显著性检验共用同一批 per_query keys
    base.per_query = tgt.per_query
    retrieval = RetrievalEval(
        arms={"bm25_only": base, "ontology_hybrid": tgt},
        embedder="bge-m3+sapbert+qwen3-vl",
        reranker="bge-reranker-v2-m3",
    )
    norm = NormalizationEval(
        total=10,
        correct=9,
        by_entity_type={"SUBSTANCE": (5, 5), "TARGET": (4, 5)},
        failures=[{"text": "bad", "expect": "HMD:1", "got": "HMD:2"}],
    )
    target = MetricTarget(
        id="ontology_gain",
        metric="ndcg_at_10",
        arm="ontology_hybrid",
        comparison="absolute_gain",
        threshold=0.05,
        baseline_arm="bm25_only",
        rationale="probe",
    )
    outcomes = [
        TargetOutcome(
            target=target,
            actual=0.40,
            baseline=0.30,
            observed=0.10,
            met=True,
        )
    ]
    return norm, retrieval, outcomes


def test_rich_render_shows_header_trace_and_sections():
    norm, retrieval, outcomes = _sample()
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None)
    render_eval(norm, retrieval, outcomes, console=console, verbose=True)
    text = buf.getvalue()
    assert "Gold Eval" in text
    assert "Trace" in text
    assert "Normalize" in text
    assert "Retrieval" in text
    assert "Targets" in text
    assert "Normalization" in text
    assert "本体增强混合" in text or "ontology" in text.lower()
    assert "ontology_gain" in text
    assert "PASS" not in text or "OK" in text or "Gold eval" in text


def test_compact_skips_detail_panels():
    norm, retrieval, outcomes = _sample()
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None)
    render_eval(norm, retrieval, outcomes, console=console, verbose=False)
    text = buf.getvalue()
    assert "Gold Eval" in text
    assert "Trace" in text
    # compact 不展开 ① Normalization 面板（含失败明细 'bad'）
    assert "① Normalization" not in text
    assert "'bad'" not in text


def test_summary_json_includes_core_fields():
    norm, retrieval, outcomes = _sample()
    payload = summary_json(norm, retrieval, outcomes)
    assert '"accuracy"' in payload
    assert '"ontology_hybrid"' in payload
    assert '"ontology_gain"' in payload
    assert '"ok"' in payload
