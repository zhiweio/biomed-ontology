"""demo Rich 渲染：对齐 foundation golden 的 CLI 面，不能退回纯 print。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from biomed_ontology.demo import DemoResult, render_demo_results, summary_json


def _sample() -> list[DemoResult]:
    return [
        DemoResult("D1", "别名一致性", "claim-a", lines=["detail a"], passed=True),
        DemoResult("D2", "层级扩展", "claim-b", lines=["detail b"], passed=False),
    ]


def test_rich_render_shows_header_trace_and_status():
    buf = StringIO()
    console = Console(file=buf, width=100, force_terminal=True, color_system=None)
    render_demo_results(_sample(), console=console, verbose=True)
    text = buf.getvalue()
    assert "Semantic Layer Demo" in text
    assert "D1" in text and "D2" in text
    assert "PASS" in text and "FAIL" in text
    assert "claim-a" in text and "detail a" in text


def test_compact_skips_per_demo_panels():
    buf = StringIO()
    console = Console(file=buf, width=100, force_terminal=True, color_system=None)
    render_demo_results(_sample(), console=console, verbose=False)
    text = buf.getvalue()
    assert "Trace" in text or "D1" in text
    # compact 不展开 detail lines 面板正文（仍可能出现在 claim 列）
    assert "detail a" not in text


def test_summary_json_includes_claim():
    payload = summary_json(_sample())
    assert '"claim": "claim-a"' in payload
    assert '"passed": false' in payload
