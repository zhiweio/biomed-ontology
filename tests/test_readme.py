"""README 里引用了实测数字。数字会变，文档不会自己跟着变。

采购数字必须以真 Milvus + GraphDB 重跑为准；本机无索引时跳过数值对照，
仍校验工具数 / demo 数 / 不回落承诺等静态契约。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from biomed_ontology.demo import DEMOS
from biomed_ontology.eval import eval_retrieval
from biomed_ontology.pipeline import build_literature_base
from biomed_ontology.tools import TOOL_SPECS

README = Path(__file__).resolve().parents[1] / "README.md"
LICENSED = frozenset({"MOCK_LICENSED"})


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def milvus_ev():
    pytest.importorskip("pymilvus")
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.eval import _chunk_key_index, load_gold
    from biomed_ontology.search.backends.milvus import MilvusBackend

    kb = build_literature_base(with_graph=False)
    index = _chunk_key_index(kb)
    gold = load_gold("retrieval")
    dangling = [
        f"{q['id']}:{k}"
        for q in gold["queries"]
        for k in (q.get("relevant") or {})
        if k not in index
    ]
    if dangling:
        pytest.skip(f"gold 与语料漂移 {len(dangling)} 条；对齐后再核 README 数字")
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=settings.milvus_uri)
        if not client.has_collection(settings.milvus_collection):
            pytest.skip("Milvus 集合未建；先 hmd index --recreate")
        backend = MilvusBackend(
            uri=settings.milvus_uri,
            collection=settings.milvus_collection,
            embedder=get_embedder("multimodal-bio"),
            known_sources=frozenset(s.id for s in kb.registry.active()),
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus 不可达：{exc}")
    ev = eval_retrieval(kb, entitlements=LICENSED, milvus_backend=backend)
    if "bm25_only" not in ev.arms or "ontology_hybrid" not in ev.arms:
        pytest.skip(f"主臂未运行：{ev.unavailable}")
    return ev


@pytest.mark.parametrize("arm", ["bm25_only", "dense_only", "ontology_hybrid"])
@pytest.mark.parametrize("metric", ["recall_at_10", "precision_at_5", "ndcg_at_10", "mrr"])
def test_readme_quotes_the_current_overall_numbers(readme, milvus_ev, arm, metric):
    value = f"{getattr(milvus_ev.arms[arm], metric):.3f}"
    assert value in readme, f"README 未包含 {arm}.{metric} 的当前实测值 {value}"


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("arm", ["bm25_only", "dense_only", "ontology_hybrid"])
def test_readme_quotes_the_current_language_split(readme, milvus_ev, lang, arm):
    """分语种表是"混合臂在英文上不如纯向量"这条结论的唯一载体。"""
    for metric in ("recall_at_10", "ndcg_at_10"):
        value = f"{getattr(milvus_ev.arms[arm].by_lang[lang], metric):.3f}"
        assert value in readme, f"README 未包含 {arm}.{lang}.{metric} 的当前值 {value}"


def test_readme_quotes_the_current_recall_lift(readme, milvus_ev):
    assert f"{milvus_ev.lift():+.1%}" in readme


def test_readme_tool_count_matches_the_contract(readme):
    assert f"{len(TOOL_SPECS)} 个工具" in readme
    assert f"× {len(TOOL_SPECS)}" in readme


def test_readme_demo_count_matches_the_registry(readme):
    assert f"{len(DEMOS)} 个演示场景" in readme


def test_readme_test_count_matches_reality(readme, request):
    """写死总数是为了让"某条测试悄悄不再跑了"这件事被看见。"""
    claimed = re.search(r"\*\*(\d+) 条测试\*\*", readme)
    assert claimed, "README 未声明测试总数"
    if request.config.getoption("file_or_dir"):
        pytest.skip("只在全量 pytest 下校验总数")
    assert request.session.testscollected == int(claimed.group(1))


def test_readme_does_not_promise_a_milvus_fallback(readme):
    """回落会让报告里的"Milvus 三列"其实是本地跑的。README 必须说的是同一件事。"""
    assert "绝不回落" in readme or "不回落" in readme


def test_readme_carries_the_knowhere_attribution(readme):
    """Apache 2.0 §4(b) 要求标注衍生与修改，README 至少要把读者指到 NOTICE。"""
    assert "Ontos-AI/knowhere" in readme
    assert "Apache" in readme
    assert "NOTICE" in readme


def test_readme_flags_the_pending_license_reviews(readme):
    """MinerU / PyMuPDF / BiomedCLIP 的义务尚未核实。README 不得把它说成已经清楚。"""
    assert "MinerU" in readme and "PyMuPDF" in readme and "BiomedCLIP" in readme
    assert "待法务核实" in readme
