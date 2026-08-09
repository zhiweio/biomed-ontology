"""双面 Eval：Identity + Bridge + suite 编排（不吞并 golden-eval）。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from biomed_ontology.eval import DualEvalReport, eval_bridge, eval_identity, run_dual_eval
from biomed_ontology.eval.render import render_dual_eval
from biomed_ontology.runtime import open_dual_surface


def test_identity_gate_resolves_golden_aliases() -> None:
    surface = open_dual_surface()
    ev = eval_identity(surface.foundation)
    assert ev.gate_ok
    assert ev.gate_total >= 4
    assert all(row["ok"] for row in ev.cases if row.get("gate"))


def test_bridge_alias_and_literature() -> None:
    surface = open_dual_surface()
    ev = eval_bridge(surface, entitlements=frozenset({"MOCK_LICENSED"}))
    assert ev.alias_ok
    assert ev.literature_ok
    assert ev.ok


def test_run_dual_eval_without_literature() -> None:
    surface = open_dual_surface()
    report = run_dual_eval(
        surface,
        entitlements=frozenset({"MOCK_LICENSED"}),
        suites=("identity", "bridge"),
    )
    assert report.literature is None
    assert report.ok
    assert "identity" in report.suites_run
    data = report.to_dict()
    assert data["policy"]["world_model_gate"] == "hmd foundation golden-eval"
    assert data["identity"]["gate_ok"] is True


def test_render_dual_eval_mentions_golden_eval() -> None:
    report = DualEvalReport(
        suites_run=["identity", "bridge"],
    )
    # minimal identity/bridge via live surface for richer panel
    surface = open_dual_surface()
    report = run_dual_eval(
        surface,
        entitlements=frozenset({"MOCK_LICENSED"}),
        suites=("identity", "bridge"),
    )
    buf = StringIO()
    cons = Console(file=buf, force_terminal=True, width=100, color_system=None)
    render_dual_eval(report, console=cons, verbose=True)
    text = buf.getvalue()
    assert "Dual-Surface Eval" in text
    assert "Identity" in text
    assert "Bridge" in text
    assert "golden-eval" in text
