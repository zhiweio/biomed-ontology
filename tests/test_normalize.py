"""四级级联归一化。

这一层的失败模式有两种：错判和漏判。
错判会顺着链路一路变成带凭据的错误结论，漏判只是少一条召回 ——
所以下面多数断言在意的是"该弃权时有没有弃权"。
"""

from __future__ import annotations

import pytest

from biomed_ontology.alias import (
    SCOPE_WEIGHTS,
    contains_cjk,
    generate_code_variants,
    normalize_alias,
)
from biomed_ontology.normalize.matchers import (
    PRIOR_ONLY_CONFIDENCE_CAP,
    has_entity_shape,
    maximal_spans,
    zh_segment_bounded,
)

SAVOLITINIB = "HMD:ENT:DC:savolitinib"
MET = "HMD:ENT:TGT:MET"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Savolitinib", "savolitinib"),
        ("  SAVOLITINIB  ", "savolitinib"),
        ("AZD-6094", "azd6094"),
        ("AZD 6094", "azd6094"),
        ("沃利替尼", "沃利替尼"),
        ("Non-Small Cell Lung Cancer", "nonsmallcelllungcancer"),
    ],
)
def test_normalize_alias(raw, expected):
    assert normalize_alias(raw) == expected


def test_code_variants_cover_common_writings():
    """研发代号在文献里的写法差异是别名漏召回的头号来源。"""
    v = {normalize_alias(x) for x in generate_code_variants("AZD-6094")}
    assert normalize_alias("AZD6094") in v
    assert normalize_alias("AZD 6094") in v


def test_broad_and_related_scopes_do_not_contribute_to_retrieval():
    """上位词参与检索会把"肺癌"的文档全算成"非小细胞肺癌"的命中。"""
    assert SCOPE_WEIGHTS["EXACT"] == 1.0
    assert SCOPE_WEIGHTS["BROAD"] == 0.0
    assert SCOPE_WEIGHTS["RELATED"] == 0.0


def test_contains_cjk():
    assert contains_cjk("沃利替尼")
    assert not contains_cjk("savolitinib")


# ------------------------------------------------------------------ 归一化行为


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Savolitinib", SAVOLITINIB),
        ("savolitinib", SAVOLITINIB),
        ("AZD6094", SAVOLITINIB),
        ("AZD-6094", SAVOLITINIB),
        ("沃利替尼", SAVOLITINIB),
        ("volitinib", SAVOLITINIB),
    ],
)
def test_all_writings_collapse_to_one_code(kb, ctx, text, expected):
    """同一物质的六种写法必须落到同一个 code —— 这是整个语义层的第一性承诺。"""
    r = kb.normalizer.normalize(text, ctx=ctx)
    assert r.matched[0].concept_id == expected


def test_unknown_term_yields_no_match_rather_than_a_guess(kb, ctx):
    r = kb.normalizer.normalize("完全不存在的化合物名 XYZQ", ctx=ctx)
    assert not r.matched or r.matched[0].confidence < 0.5


def test_ambiguous_met_resolves_by_context(kb, ctx):
    r = kb.normalizer.normalize("MET", ctx=ctx, context="MET exon 14 skipping 肿瘤 靶点")
    assert r.matched[0].concept_id == MET
    assert r.matched[0].confidence > 0.9


def test_ambiguous_met_abstains_on_out_of_ontology_sense(kb, ctx):
    """ "代谢当量"不在本体里 —— 此时正确行为是弃权而不是挑一个最像的。

    猜一个会产出一条带凭据的错误结论；弃权只会产出一条 unmapped 信号，
    而那条信号恰好是本体该长出新概念的地方。
    """
    r = kb.normalizer.normalize(
        "MET", ctx=ctx, context="8 MET of moderate exercise, metabolic equivalent of task"
    )
    assert not r.matched or r.matched[0].concept_id != MET


def test_weak_cue_evidence_shows_up_as_low_confidence(kb, ctx):
    """零线索时先验会胜出 —— 但结果必须以低置信度呈现。

    先验赢了不是 bug（0.85 对 0.05 本就该赢），把它包装成 0.85 的置信度才是：
    那是在把"这个词通常指 MET"说成"这次它指 MET"。封顶后它会被挖成
    low_confidence_normalization 信号，"线索词覆盖不够"于是自己浮上来。
    """
    r = kb.normalizer.normalize("MET", ctx=ctx, context="本节讨论若干指标")
    assert r.matched and r.matched[0].concept_id == MET
    assert r.matched[0].confidence <= PRIOR_ONLY_CONFIDENCE_CAP


def test_document_mode_honours_caller_context(kb, ctx):
    """文档模式必须把调用方 context 并进消歧线索。

    只拿正文当上下文会让 context 被整段稀释掉，短文本上尤其致命 ——
    这正是 D4 场景暴露出来的真实回归。
    """
    r = kb.normalizer.normalize(
        "8 MET of moderate exercise",
        ctx=ctx,
        context="运动 代谢当量 metabolic equivalent of task",
        detect=True,
    )
    assert all(m.concept_id != MET for m in r.matched)


def test_expansion_includes_descendants(kb, ctx):
    terms = kb.normalizer.expand("HMD:ENT:IND:lung_cancer", ctx=ctx)
    ids = {t.concept_id for t in terms}
    assert "HMD:ENT:IND:nsclc" in ids, "肺癌应扩展到非小细胞肺癌"


def test_descendants_are_transitive(kb):
    d = kb.normalizer.descendants("HMD:ENT:IND:lung_cancer", max_depth=3)
    assert "HMD:ENT:IND:lung_adenocarcinoma" in d, "应经由 NSCLC 到达肺腺癌"


# ------------------------------------------------------------------ 跨度识别


@pytest.mark.parametrize("text", ["ABT-869", "Zanubrutinib", "BTK", "泽布替尼"])
def test_entity_shaped_spans_are_admitted(text):
    assert has_entity_shape(text)


@pytest.mark.parametrize(
    "text", ["一种新", "中的含义", "制剂", "联用", "the", "of the", "结果", "本发明"]
)
def test_function_words_and_generic_terms_are_rejected(text):
    """未识别跨度会直接变成本体演进的候选。

    放行泛化词，curator 收到的就是一屏"制剂""联用"这类噪音，
    几轮之后没人再看这个队列 —— 演进闭环就此失效。
    """
    assert not has_entity_shape(text)


def test_zh_segment_bounded_requires_maximal_segment():
    text = "本发明涉及泽布替尼的制备方法"
    i = text.index("泽布替尼")
    assert zh_segment_bounded(text, i, i + 4)
    assert not zh_segment_bounded(text, i, i + 2)  # "泽布" 是片段的一部分


def test_maximal_spans_drops_covered_substrings():
    spans = [("Zanubrutinib", 0, 12), ("Zanu", 0, 4), ("BTK", 20, 23)]
    kept = maximal_spans(spans)
    assert ("Zanu", 0, 4) not in kept
    assert ("BTK", 20, 23) in kept


def test_detect_mode_grounds_known_entities(kb, ctx):
    r = kb.normalizer.normalize("Savolitinib inhibits MET in NSCLC patients.", ctx=ctx, detect=True)
    assert {m.concept_id for m in r.matched} >= {SAVOLITINIB, MET}


def test_every_match_carries_a_decision_record(kb, ctx):
    """没有决策记录的匹配等于不可解释的匹配。"""
    before = len(ctx.decisions)
    kb.normalizer.normalize("沃利替尼", ctx=ctx)
    assert len(ctx.decisions) > before
