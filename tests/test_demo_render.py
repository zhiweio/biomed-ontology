"""demo Rich 渲染：通用行分类，不能退回纯 print，也不能按 demo_id 写死。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from biomed_ontology.demo import DemoResult, render_demo_results, summary_json
from biomed_ontology.demo.render import (
    _classify_line,
    _parse_concepts,
    _parse_fact,
    _parse_hit,
    _parse_metrics,
    _parse_restore,
    _parse_signal,
    _parse_tree,
)


def _sample() -> list[DemoResult]:
    return [
        DemoResult("D1", "别名一致性", "claim-a", lines=["detail a"], passed=True),
        DemoResult("D2", "层级扩展", "claim-b", lines=["detail b"], passed=False),
    ]


def _mixed_sample() -> list[DemoResult]:
    """覆盖真实行形态（含 snippet 内嵌换行），不绑定 demo_id 分支。"""
    return [
        DemoResult(
            "W2",
            "World Model · context",
            "savolitinib 上下文含 targets 与可引用 evidence（三后端）",
            lines=[
                "targets=1 evidence=6 backends={'entity': 'graphdb', 'relationships': "
                "'graphdb', 'related': 'graphdb', 'evidence': 'milvus', "
                "'assets': 'openmetadata', 'bios': 'graphdb_biomedical'}",
            ],
            passed=True,
        ),
        DemoResult(
            "Dx",
            "generic lines",
            "通用行分类冒烟",
            lines=[
                "语料扩展：['lung cancer', 'NSCLC']",
                "不过滤时前十模态构成：{'IMAGE': 2, 'TEXT': 8}",
                "  [IMAGE] DOC:PMID.1#image:F2 :: Kaplan-Meier curve of PFS",
                "  + DOC:PMC1#Abstract :: In 2022, it was found that in the United States,\napp",
                "      接地概念 ['HMD:ENT:IND:lung_cancer', 'HMD:ENT:IND:nsclc']",
                "  + DOC:PMC2#Sec :: Created with BioRender\nThe International Agency for ",
                "      接地概念 ['HMD:ENT:IND:sclc']",
                "modalities=[IMAGE] 时命中 2 条",
                "  fruquintinib -has_target-> kinase insert domain receptor [TEXT] ← "
                "DOC:PMID.30006302 p1 Abstract",
                "  DOC:CTGOV.NCT1 碎片 1 个 → 章节 1 处：BriefSummary",
                "还原 CHK:txt.abc：BriefSummary p1-1，10 字碎片 → 20 字全节（截断=False）",
                "  [P0] cooccurrence_anomaly 'HMD:ENT:IND:nsclc~HMD:ENT:TGT:MET' x6",
                "    create edge HMD:ENT:IND:nsclc hmd:related_to HMD:ENT:TGT:MET",
                "HMPL-504 → HMD:ENT:DC:savolitinib",
                "KB concept_id=HMD:ENT:DC:savolitinib",
                "事实：无凭据 7 条（过滤 1，最高 TIER_0） / 有凭据 8 条（最高 TIER_3）",
            ],
            passed=True,
        ),
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
    assert "detail a" not in text


def test_summary_json_includes_claim():
    payload = summary_json(_sample())
    assert '"claim": "claim-a"' in payload
    assert '"passed": false' in payload


def test_parsers_are_generic_not_demo_id_bound():
    # snippet 内嵌换行仍应识别为 hit
    hit = _parse_hit(
        "  + DOC:PMC1#Abstract :: In 2022, it was found that in the United States,\napp"
    )
    assert hit is not None
    assert hit["ref"].startswith("DOC:PMC1")
    assert "United States" in (hit["snip"] or "")

    assert _parse_concepts("      接地概念 ['HMD:ENT:IND:nsclc']") == ["HMD:ENT:IND:nsclc"]
    fact = _parse_fact("  fruquintinib -has_target-> MET [TEXT] ← DOC:PMID.1 p1 Abstract")
    assert fact is not None and fact["predicate"] == "has_target"
    tree = _parse_tree("  DOC:CTGOV.NCT1 碎片 1 个 → 章节 1 处：BriefSummary")
    assert tree is not None and tree["chunks"] == "1"
    signal = _parse_signal("  [P0] cooccurrence_anomaly 'HMD:ENT:IND:nsclc~HMD:ENT:TGT:MET' x6")
    assert signal is not None and signal["priority"] == "P0"

    metrics = _parse_metrics(
        "targets=1 evidence=6 backends={'entity': 'graphdb', 'evidence': 'milvus'}"
    )
    assert metrics is not None
    keys = [k for k, _ in metrics["pairs"]]
    assert keys == ["targets", "evidence", "backends"]

    restore_line = "还原 CHK:txt.abc：BriefSummary p1-1，10 字碎片 → 20 字全节（截断=False）"
    assert _classify_line(restore_line)[0] == "restore"
    restore = _parse_restore(restore_line)
    assert restore is not None and restore["chunk"] == "CHK:txt.abc"
    assert _classify_line("HMPL-504 → HMD:ENT:DC:savolitinib")[0] == "arrow"
    assert _classify_line("KB concept_id=HMD:ENT:DC:savolitinib")[0] == "metrics"


def test_mixed_lines_render_structured_panels():
    buf = StringIO()
    console = Console(file=buf, width=140, force_terminal=True, color_system=None)
    render_demo_results(_mixed_sample(), console=console, verbose=True)
    text = buf.getvalue()

    assert "entity=graphdb" in text or "graphdb" in text
    assert "entity×graphdb" not in text
    assert "Hits" in text
    # 换行 snippet + 接地概念挂载后应合并成一张表，而不是碎成多个 Hits 1
    assert "Hits  2" in text or "Hits  3" in text or "Hits  4" in text
    assert "IMAGE×2" in text and "TEXT×8" in text
    assert "NSCLC" in text  # list chips
    assert "Facts" in text
    assert "has_target" in text
    assert "Evidence tree" in text
    assert "Restore" in text
    assert "Signals" in text
    assert "KGCL" in text
    assert "Resolve" in text
    assert "free" in text and "paid" in text
