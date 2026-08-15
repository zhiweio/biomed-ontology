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


def test_locally_placed_weights_win_over_any_download(monkeypatch, tmp_path):
    """内网里手动拷权重是常态，应该走"放对位置"而不是"改代码"。

    这条同时是联网保险：只要目录在，resolve_model 一步都不该往外走。
    """
    from biomed_ontology import config, embed

    target = tmp_path / "models" / "SapBERT-from-PubMedBERT-fulltext"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        config, "settings", config.Settings(model_hub="hf", model_cache_dir=tmp_path)
    )

    assert embed.resolve_model("cambridgeltl/SapBERT-from-PubMedBERT-fulltext") == str(
        target.resolve()
    )


def test_hf_hub_cache_layout_is_recognized_without_download(monkeypatch, tmp_path):
    """snapshot_download(cache_dir=X) 落在 X/models--org--name/snapshots/<rev>。

    只认 models/<名> 的话，HF 下过的权重下一轮还是会再打 Hub。
    """
    from pathlib import Path

    from biomed_ontology import config, embed

    snap = tmp_path / "models--BAAI--bge-m3" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        config, "settings", config.Settings(model_hub="hf", model_cache_dir=tmp_path)
    )

    assert Path(embed.resolve_model("BAAI/bge-m3")).resolve() == snap.resolve()


def test_relative_model_cache_dir_does_not_follow_cwd(monkeypatch, tmp_path):
    """Prefect process worker 的 cwd 经常不是仓库。相对缓存必须锚在仓库根。"""
    from pathlib import Path

    from biomed_ontology import config, embed

    monkeypatch.setattr(config, "_REPO_ROOT", tmp_path)
    rel = Path("cache/models")
    target = tmp_path / rel / "models" / "bge-m3"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "settings", config.Settings(model_hub="hf", model_cache_dir=rel))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert Path(embed.resolve_model("BAAI/bge-m3")).resolve() == target.resolve()


def test_unregistered_model_fails_loudly_on_a_mirror(monkeypatch, tmp_path):
    """镜像站的命名空间与 HF 无关，猜不出来 —— 没登记就报错，别去 clone 一个不存在的仓库。"""
    from biomed_ontology import config, embed

    monkeypatch.setattr(
        config, "settings", config.Settings(model_hub="gitee", model_cache_dir=tmp_path)
    )
    with pytest.raises(ValueError, match="_MIRRORS"):
        embed.resolve_model("openai/clip-vit-base-patch32")


def test_modelscope_is_recorded_as_having_no_pytorch_sapbert():
    """ModelScope 上的 SapBERT 只有 Xenova 的 ONNX 版，没有 PyTorch 权重。

    写成断言而不是注释：否则下次有人"顺手"把它填回映射表，
    只会在 AutoModel 加载时炸，而报错信息完全指不到这里。
    """
    from biomed_ontology import embed

    assert embed._MIRRORS["cambridgeltl/SapBERT-from-PubMedBERT-fulltext"]["modelscope"] is None
    with pytest.raises(LookupError, match="PyTorch"):
        embed._mirror_repo("cambridgeltl/SapBERT-from-PubMedBERT-fulltext", "modelscope")


def test_interrupted_gitee_clone_leaves_no_half_model_behind(monkeypatch, tmp_path):
    """clone 中断留下的半份目录里 config.json 一定在（它不是 LFS 文件），
    会被下一次 resolve_model 当成"本地已有"直接返回 —— 那是一份 LFS 指针
    没拉下来的坏模型，要到加载时才炸。所以必须先暂存再原子改名。"""
    import subprocess
    from pathlib import Path

    from biomed_ontology import config, embed

    monkeypatch.setattr(
        config, "settings", config.Settings(model_hub="gitee", model_cache_dir=tmp_path)
    )

    def clone_then_die(cmd, **kwargs):
        if list(cmd[:3]) == ["git", "lfs", "version"]:
            return subprocess.CompletedProcess(cmd, 0)
        staged = Path(cmd[-1])
        staged.mkdir(parents=True)
        (staged / "config.json").write_text("{}", encoding="utf-8")
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", clone_then_die)
    with pytest.raises(subprocess.CalledProcessError):
        embed.resolve_model("BAAI/bge-m3")

    assert not (tmp_path / "models" / "bge-m3").exists()


def test_git_lfs_is_checked_before_the_clone_starts(monkeypatch, tmp_path):
    """没装 git-lfs 时 clone 照样"成功"，只是权重变成几百字节的指针文本 ——
    报错要推迟到 AutoModel 加载才出现，且内容与 LFS 无关。

    检查必须在 clone **之前**：否则先白拉几百 MB 再失败。
    """
    import subprocess

    from biomed_ontology import config, embed

    monkeypatch.setattr(
        config, "settings", config.Settings(model_hub="gitee", model_cache_dir=tmp_path)
    )
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd[:3]))
        if list(cmd[:3]) == ["git", "lfs", "version"]:
            raise subprocess.CalledProcessError(1, cmd)
        raise AssertionError("git-lfs 缺失时不该开始 clone")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="git-lfs"):
        embed.resolve_model("BAAI/bge-m3")

    assert seen == [["git", "lfs", "version"]]


def test_device_is_one_of_the_three_backends():
    from biomed_ontology.embed import best_device

    assert best_device() in {"cuda", "mps", "cpu"}


def test_device_detection_does_not_require_torch(monkeypatch):
    """CI 走 FakeEmbedder，不装 torch。仅仅问一句"用什么设备"
    不该把可选依赖变成必需依赖。"""
    import sys

    from biomed_ontology.embed import best_device

    monkeypatch.setitem(sys.modules, "torch", None)
    assert best_device() == "cpu"


def test_every_real_embedder_default_is_registered():
    """默认模型 ID 必须都在镜像表里，否则切到镜像源才发现漏了一个。"""
    import inspect

    from biomed_ontology.embed import _MIRRORS, BiomedEmbedder, GeneralEmbedder

    for cls in (GeneralEmbedder, BiomedEmbedder):
        default = inspect.signature(cls).parameters["model_id"].default
        assert default in _MIRRORS, f"{cls.__name__} 的默认模型未登记镜像映射"
