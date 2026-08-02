"""图型判定。

真模型不进 CI（800MB 权重），所以这里测的是**判定之外的一切**：
兜底规则、优先级、证据来源标注、以及"没有权重时这条路还走不走得通"。
零样本本身的判别力靠 `scripts/` 下的人工核对，不靠断言 —— 一个会随
模型版本漂移的准确率写进 assert 只会训练出"改数字让它绿"的反射。
"""

from __future__ import annotations

import pytest

from biomed_ontology.parse.figure_type import (
    FIGURE_TYPES,
    FigureTyping,
    KeywordFigureTyper,
    get_figure_typer,
    type_from_caption,
)


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("Figure 2. Chest CT scan at baseline showing a pulmonary nodule", "RADIOLOGY"),
        ("图 3. 治疗前后 CT 影像对比", "RADIOLOGY"),
        ("Figure 1. H&E staining of tumor tissue (200x)", "MICROSCOPY"),
        ("Figure 4. Immunohistochemistry for MET expression", "MICROSCOPY"),
        ("Figure 5. Gross specimen of the resected tumor", "GROSS_PATHOLOGY"),
        ("Figure 6. Kaplan-Meier curve of progression-free survival", "CHART"),
        ("图 2. 无进展生存曲线", "CHART"),
        ("Figure 7. Western blot of phosphorylated MET", "GEL_BLOT"),
        ("Figure 8. CONSORT flow diagram of patient enrollment", "DIAGRAM"),
        ("Table 2. Baseline characteristics", "TABLE_IMAGE"),
        ("Figure 9.", "OTHER"),
    ],
)
def test_caption_rules_cover_the_common_figure_captions(caption: str, expected: str):
    assert type_from_caption(caption).figure_type == expected


def test_specific_rules_win_over_the_generic_word_figure():
    """ "Figure" 这个词出现在几乎每条 caption 里。

    规则顺序写反的失败形态是**所有图都变成 DIAGRAM** —— 分布看起来还挺正常，
    没人会觉得不对，直到有人按 RADIOLOGY 过滤发现一条都没有。
    """
    assert type_from_caption("Figure 3. CT scan of the chest").figure_type == "RADIOLOGY"
    assert type_from_caption("Figure 3. H&E stained section").figure_type == "MICROSCOPY"


def test_gross_specimen_is_not_filed_as_microscopy():
    """大体标本和镜检都是"组织的样子"，但一个是肉眼一个是显微镜下。

    实测里 BiomedCLIP 在没有 GROSS_PATHOLOGY 这一类时把切除标本照片
    判成了 MICROSCOPY（0.356）—— 这条守的是那次修复。
    """
    assert type_from_caption("Gross appearance of the resected specimen").figure_type == (
        "GROSS_PATHOLOGY"
    )


def test_every_declared_type_is_reachable_from_a_caption():
    """声明了却没有任何输入能命中的类型，等于一个永远为空的过滤值。"""
    reachable = {
        type_from_caption(c).figure_type
        for c in (
            "chest CT scan",
            "H&E staining",
            "gross specimen photograph",
            "Kaplan-Meier survival curve",
            "western blot",
            "CONSORT flow diagram",
            "Table 1",
        )
    }
    assert reachable == set(FIGURE_TYPES) - {"OTHER"}


def test_text_chunks_get_no_figure_type_at_all():
    """没有图的切片是空串，不是 OTHER。

    OTHER 的意思是"是图但认不出类型"，空串是"根本不是图"。
    两者合并之后，"按图型过滤"会把全部正文一起放进来。
    """
    typer_impl = KeywordFigureTyper()
    out = typer_impl.classify([None, "/tmp/x.png"], ["正文段落", "Figure 1. CT scan"])
    assert out[0] == FigureTyping("", 0.0, "none")
    assert out[1].figure_type == "RADIOLOGY"


def test_source_is_recorded_so_the_two_kinds_of_label_stay_distinguishable():
    """零样本给的和关键词兜底给的可信度完全不同，混进一个字段就再也分不开。"""
    assert type_from_caption("Figure 1. CT scan").source == "caption"
    assert type_from_caption("Figure 1.").source == "none"


def test_mismatched_lengths_fail_loudly():
    with pytest.raises(ValueError, match="长度必须一致"):
        KeywordFigureTyper().classify([None, None], ["only one caption"])


def test_default_typer_needs_no_model_weights():
    """默认必须是不下模型也能用的那个 —— 与 `get_embedder` 默认 fake 同一个理由。

    默认成 biomedclip 的后果是：任何只想跑一遍索引的人都得先下 800MB，
    而 `figure_type` 只是个附加字段。
    """
    assert get_figure_typer().name == "caption"


def test_unknown_typer_is_rejected_rather_than_silently_downgraded():
    with pytest.raises(ValueError, match="未知 figure typer"):
        get_figure_typer("clip-but-better")
