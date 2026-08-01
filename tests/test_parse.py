"""解析内核：语义树构建的性质。

这里的测试大多不碰 PDF —— 版面后端产出 `LayoutBlock` 之后，
树构建是纯函数。把它测透，两个后端就都被测到了。
"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_fact import HeadingSourceEnum
from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse import build_tree, merge_candidates
from biomed_ontology.parse.layout.base import LayoutBlock, LayoutResult
from biomed_ontology.parse.nodes import dedupe_same_as
from biomed_ontology.parse.outline import extract_toc_nodes, grep_headings
from biomed_ontology.parse.skeleton import fat_leaves


def _layout(*blocks: LayoutBlock, pages: int = 10, backend: str = "pymupdf") -> LayoutResult:
    from pathlib import Path

    return LayoutResult(blocks=blocks, assets_dir=Path("/tmp/x"), page_count=pages, backend=backend)


def _h(text: str, page: int, level: int, **meta: object) -> LayoutBlock:
    return LayoutBlock(
        kind="heading", text="#" * level + " " + text, page=page, level=level, backend_meta=meta
    )


def _t(text: str, page: int) -> LayoutBlock:
    return LayoutBlock(kind="text", text=text, page=page)


# ------------------------------------------------------------------ 骨架构建


def test_missing_toc_falls_back_to_layout_signals():
    """PMC 的 PDF 十有八九没有内嵌书签，这是常态而非异常。"""
    layout = _layout(_h("Methods", 2, 1), _t("Patients received 600 mg.", 2))
    skeleton, leaves = build_tree(layout, toc=[])
    assert [s.title for s in skeleton] == ["Methods"]
    assert "600 mg" in leaves[0].text


def test_document_without_any_heading_yields_one_synthetic_section():
    """返回空列表会让"没有章节"和"解析失败"长得一样。"""
    skeleton, leaves = build_tree(_layout(_t("plain text", 1)), root_title="Doc")
    assert len(skeleton) == 1
    assert skeleton[0].heading_source == HeadingSourceEnum.SYNTHETIC
    assert skeleton[0].heading_confidence < 0.5
    assert "plain text" in leaves[0].text


def test_level_jump_is_flattened_not_filled_with_a_fake_node():
    """H1 → H3 压平成 H2。凭空插节点会让 section_path 指向原文不存在的章节。"""
    layout = _layout(_h("Results", 3, 1), _h("Subgroup", 4, 3))
    skeleton, _ = build_tree(layout)
    assert [s.level for s in skeleton] == [1, 2]
    assert skeleton[1].section_path == "Results / Subgroup"
    assert len(skeleton) == 2  # 没有第三个"补位"节点


def test_parent_page_range_covers_its_children():
    """闭区间必须按同级或更高级标题收口，否则父章节会被子章节挖空。"""
    layout = _layout(_h("Results", 2, 1), _h("Safety", 4, 2), _h("Discussion", 8, 1), pages=10)
    skeleton, _ = build_tree(layout)
    results, safety, discussion = skeleton
    assert (results.start_page, results.end_page) == (2, 7)
    assert safety.start_page >= results.start_page
    assert safety.end_page <= results.end_page
    assert discussion.start_page == 8


def test_duplicate_titles_get_distinct_paths():
    """section_path 是主键，同名章节碰撞会让两节内容混在一起。"""
    layout = _layout(_h("Table", 1, 1), _h("Table", 5, 1))
    skeleton, _ = build_tree(layout)
    assert len({s.section_path for s in skeleton}) == 2


def test_text_is_assigned_to_the_deepest_covering_section():
    layout = _layout(
        _h("Results", 2, 1), _h("Safety", 4, 2), _t("Grade 3 events in 41%.", 4), pages=6
    )
    _, leaves = build_tree(layout)
    by_path = {n.section_path: n for n in leaves}
    assert "41%" in by_path["Results / Safety"].text
    assert "41%" not in by_path["Results"].text


def test_headings_do_not_leak_into_body_text():
    layout = _layout(_h("Methods", 1, 1), _t("body", 1))
    _, leaves = build_tree(layout)
    assert leaves[0].text.strip() == "body"


def test_fat_leaf_is_flagged_for_refinement():
    """胖叶子是"内部结构没识别出来"的信号，不是错误。"""
    layout = _layout(_h("Description", 1, 1), pages=60)
    skeleton, _ = build_tree(layout)
    assert [n.title for n in fat_leaves(skeleton, max_pages=12)] == ["Description"]


# ------------------------------------------------------------------ 候选合票


def test_embedded_toc_outranks_layout_guess():
    toc = [[1, "Introduction", 2]]
    layout = _layout(_h("Introduction", 2, 3))
    merged = merge_candidates(extract_toc_nodes(toc), grep_headings(layout))
    assert len(merged) == 1
    assert merged[0].level == 1
    assert merged[0].source == HeadingSourceEnum.TOC_EXACT


def test_multi_source_agreement_raises_confidence_but_never_to_certainty():
    toc = [[1, "Methods", 2]]
    layout = _layout(_h("Methods", 2, 1))
    only_toc = merge_candidates(extract_toc_nodes(toc))[0]
    both = merge_candidates(extract_toc_nodes(toc), grep_headings(layout))[0]
    assert both.confidence > only_toc.confidence
    assert both.confidence < 1.0, "没有哪种启发式配得上确定性"


def test_losing_candidates_are_kept_as_evidence():
    """排查"为什么这节被判成 H2"时，被否决的候选往往才是关键线索。"""
    toc = [[1, "Results", 3]]
    layout = _layout(_h("Results", 3, 2, font_size=14.0, bold=True))
    merged = merge_candidates(extract_toc_nodes(toc), grep_headings(layout))[0]
    joined = " ".join(merged.evidence)
    assert "书签" in joined
    assert "字号" in joined


def test_heading_decisions_land_in_the_trace():
    """标题层级怎么定的，必须是可审计的 WHY，而不是埋在函数里。"""
    ctx = TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")
    layout = _layout(_h("Methods", 2, 1))
    merge_candidates(extract_toc_nodes([[1, "Methods", 2]]), grep_headings(layout), ctx=ctx)
    steps = [d.stage for d in ctx.decisions]
    assert "parse.heading" in steps
    assert ctx.decisions[0].candidates, "只记结果不记候选，就回答不了「为什么没选那个」"


def test_canonical_section_names_beat_arbitrary_bold_text():
    layout = _layout(_h("Discussion", 6, 2), _h("Some bold caption", 6, 2))
    merged = {c.title: c.confidence for c in merge_candidates(grep_headings(layout))}
    assert merged["Discussion"] > merged["Some bold caption"]


def test_numbered_body_lines_are_recognised_as_headings():
    layout = _layout(_t("3.2 Pharmacokinetics", 5), _t("二、发明内容", 6))
    merged = merge_candidates(grep_headings(layout))
    titles = [c.title for c in merged]
    assert "3.2 Pharmacokinetics" in titles
    assert "二、发明内容" in titles
    assert next(c for c in merged if c.title.startswith("3.2")).level == 2


def test_long_sentences_are_never_headings():
    long_line = "Savolitinib is a selective MET tyrosine kinase inhibitor evaluated in " * 3
    assert merge_candidates(grep_headings(_layout(_t(long_line, 1)))) == []


# ------------------------------------------------------------------ SAME-AS


def test_duplicate_text_points_at_owner_instead_of_being_deleted():
    """删掉重复会破坏引用还原：碎片必须能回到它实际所在的位置。"""
    body = "Savolitinib inhibits MET phosphorylation at nanomolar concentrations in vitro."
    same_as = dedupe_same_as([("SEC:a", body), ("SEC:b", body)])
    assert same_as == {"SEC:b": "SEC:a"}
    assert "SEC:a" not in same_as, "首现者不该指向任何人"


def test_short_repeated_strings_are_not_deduped():
    """ "Results"、"n=12" 撞车是常态，判成重复会误折叠真实内容。"""
    assert dedupe_same_as([("SEC:a", "Results"), ("SEC:b", "Results")]) == {}


def test_whitespace_differences_do_not_defeat_dedupe():
    a = "The objective response rate was assessed by an independent review committee."
    b = "The  objective   response rate was assessed by an independent review committee."
    assert dedupe_same_as([("SEC:a", a), ("SEC:b", b)]) == {"SEC:b": "SEC:a"}


# ------------------------------------------------------- 跨后端一致性（验收项）


@pytest.mark.parametrize("backend", ["pymupdf", "mineru"])
def test_tree_shape_is_backend_independent(backend: str):
    """同样的版面块，两个后端必须给出同一棵树。

    差异只允许出现在 `degraded` 声明的能力上 —— 后端只负责产出块，
    层级判定与归并逻辑完全共享。
    """
    blocks = (
        _h("Abstract", 1, 1),
        _t("Savolitinib is a selective MET inhibitor.", 1),
        _h("Methods", 2, 1),
        _h("Patients", 2, 2),
        _t("Eligible patients received 600 mg once daily.", 2),
    )
    skeleton, leaves = build_tree(_layout(*blocks, backend=backend))
    assert [s.section_path for s in skeleton] == [
        "Abstract",
        "Methods",
        "Methods / Patients",
    ]
    assert {n.section_path for n in leaves if n.text}


def test_degraded_capabilities_survive_into_the_emitted_document():
    from pathlib import Path

    from biomed_ontology._generated.hmd_concept import LicenseTierEnum
    from biomed_ontology._generated.hmd_fact import DocTypeEnum, LanguageEnum
    from biomed_ontology.parse.emit import emit_document

    layout = LayoutResult(
        blocks=(_h("Methods", 1, 1), _t("body text here", 1)),
        assets_dir=Path("/tmp/x"),
        page_count=2,
        backend="pymupdf",
        degraded=("formula",),
    )
    skeleton, leaves = build_tree(layout)
    parsed = emit_document(
        doc_id="DOC:TEST.1",
        source_id="PMC",
        title="T",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        license_tier=LicenseTierEnum.TIER_0,
        language=LanguageEnum.en,
        skeleton=skeleton,
        leaves=leaves,
        layout=layout,
    )
    assert parsed.to_yaml_obj()["parse"]["degraded"] == ["formula"]


def test_emitted_yaml_matches_handwritten_corpus_schema():
    """解析产物必须能被既有 load_corpus 直接读回，否则"解析出来了"不等于"解析对了"。"""
    from pathlib import Path

    import yaml

    from biomed_ontology._generated.hmd_concept import LicenseTierEnum
    from biomed_ontology._generated.hmd_fact import DocTypeEnum, LanguageEnum
    from biomed_ontology.corpus import chunk_document, load_corpus
    from biomed_ontology.parse.emit import emit_document

    layout = _layout(
        _h("Abstract", 1, 1),
        _t("Fruquintinib is a highly selective VEGFR inhibitor for colorectal cancer.", 1),
        pages=3,
    )
    skeleton, leaves = build_tree(layout)
    parsed = emit_document(
        doc_id="DOC:TEST.2",
        source_id="PMC",
        title="Fruquintinib study",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        license_tier=LicenseTierEnum.TIER_0,
        language=LanguageEnum.en,
        skeleton=skeleton,
        leaves=leaves,
        layout=layout,
    )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "parsed.yaml"
        path.write_text(yaml.safe_dump(parsed.to_yaml_obj(), allow_unicode=True), encoding="utf-8")
        docs = load_corpus(path)

    assert len(docs) == 1
    assert docs[0].doc_id == "DOC:TEST.2"
    assert chunk_document(docs[0]), "解析产物必须能走通既有切片管线"
