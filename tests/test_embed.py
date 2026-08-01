"""向量化：确定性、维度、列缺失语义。

这里不测语义质量 —— 那是 P13 十臂消融的事。这里测的是管线接线，
而接线错了往往比模型差更难发现：召回下降会被归咎于"模型不行"。
"""

from __future__ import annotations

import pytest

from biomed_ontology.embed import VECTOR_FIELDS, FakeEmbedder, get_embedder


def test_fake_embedder_is_deterministic_across_calls():
    """CI 里向量必须可复现，否则评测数字每次都不一样。"""
    a = FakeEmbedder().encode(["savolitinib inhibits MET"])[0]
    b = FakeEmbedder().encode(["savolitinib inhibits MET"])[0]
    assert a["dense_general"] == b["dense_general"]
    assert a["sparse_lexical"] == b["sparse_lexical"]


def test_all_three_columns_are_produced_in_one_pass():
    bundle = FakeEmbedder().encode(["text"])[0]
    assert set(bundle) == set(VECTOR_FIELDS)


def test_dense_vectors_are_normalised():
    """余弦度量下未归一化的向量会让距离失去可比性。"""
    bundle = FakeEmbedder().encode(["fruquintinib VEGFR inhibitor colorectal"])[0]
    for field in ("dense_general", "dense_biomed"):
        norm = sum(v * v for v in bundle[field]) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)


def test_two_towers_have_different_dimensions():
    """双塔的意义在于两个不同的表示空间，同维同值就等于白算一遍。"""
    e = FakeEmbedder()
    assert e.dims["dense_general"] != e.dims["dense_biomed"]
    bundle = e.encode(["MET exon 14 skipping"])[0]
    assert bundle["dense_general"] != bundle["dense_biomed"]


def test_similar_texts_are_closer_than_unrelated_ones():
    """假向量也得保住"共享词 → 更接近"这条性质，否则连接线都测不了。"""
    e = FakeEmbedder()
    a, b, c = e.encode(
        [
            "savolitinib MET inhibitor lung cancer",
            "savolitinib MET inhibitor lung tumour",
            "quarterly procurement budget approval process",
        ]
    )

    def dot(x, y):
        return sum(p * q for p, q in zip(x["dense_general"], y["dense_general"], strict=True))

    assert dot(a, b) > dot(a, c)


def test_empty_text_does_not_produce_a_zero_vector():
    """零向量在余弦距离下是未定义的，会让检索静默返回垃圾。"""
    bundle = FakeEmbedder().encode([""])[0]
    assert any(v != 0.0 for v in bundle["dense_general"])


def test_sparse_vector_keeps_exact_terms_distinct():
    """精确术语不能被语义抹平 —— 这正是保留词法列的理由。"""
    e = FakeEmbedder()
    a, b = e.encode(["MET exon 14 skipping", "EGFR exon 19 deletion"])
    assert set(a["sparse_lexical"]) != set(b["sparse_lexical"])


def test_chinese_text_is_tokenised_per_character():
    """中文没有空格。按空格切会把整句当一个词，稀疏列直接失效。"""
    bundle = FakeEmbedder().encode(["索凡替尼治疗神经内分泌瘤"])[0]
    assert len(bundle["sparse_lexical"]) > 3


def test_unknown_embedder_fails_loudly():
    with pytest.raises(ValueError, match="未知 embedder"):
        get_embedder("word2vec")


def test_default_embedder_needs_no_model_download():
    """默认必须零依赖：不下 GB 级权重也能跑通全链路。"""
    assert get_embedder().name == "fake"


def test_hf_hub_passes_the_repo_id_through_untouched():
    from biomed_ontology.embed import resolve_model

    assert resolve_model("BAAI/bge-m3") == "BAAI/bge-m3"


def test_unmapped_model_fails_loudly_instead_of_falling_back_to_hf(monkeypatch):
    """内网连不上 huggingface.co 时，回落只会换来一次必然失败的长超时 ——
    那看起来像"卡住了"，而不是"这个模型没登记镜像"。"""
    from biomed_ontology import config, embed

    monkeypatch.setattr(config, "settings", config.Settings(model_hub="modelscope"))
    with pytest.raises(ValueError, match="_MODELSCOPE_IDS"):
        embed.resolve_model("openai/clip-vit-base-patch32")


def test_every_real_embedder_default_is_mirrored():
    """默认模型 ID 必须都在镜像表里，否则切到 modelscope 才发现漏了一个。"""
    import inspect

    from biomed_ontology.embed import _MODELSCOPE_IDS, BiomedEmbedder, GeneralEmbedder

    for cls in (GeneralEmbedder, BiomedEmbedder):
        default = inspect.signature(cls).parameters["model_id"].default
        assert default in _MODELSCOPE_IDS, f"{cls.__name__} 的默认模型未登记 ModelScope 映射"
