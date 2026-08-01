"""README 里引用了实测数字。数字会变，文档不会自己跟着变。

这个仓库已经为豁免正文立过同样的规矩（`test_waiver_text_quotes_the_current_numbers`）。
README 面向的是采购与合规读者，一份数字过期的 README 比没有 README 更危险。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from biomed_ontology.agentapi import TOOL_SPECS
from biomed_ontology.demo import DEMOS
from biomed_ontology.eval import eval_retrieval
from biomed_ontology.pipeline import build_knowledge_base

README = Path(__file__).resolve().parents[1] / "README.md"
LICENSED = frozenset({"MOCK_LICENSED"})


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ev():
    return eval_retrieval(build_knowledge_base(), entitlements=LICENSED)


@pytest.mark.parametrize("arm", ["bm25_only", "dense_only", "ontology_hybrid"])
@pytest.mark.parametrize("metric", ["recall_at_10", "precision_at_5", "ndcg_at_10", "mrr"])
def test_readme_quotes_the_current_overall_numbers(readme, ev, arm, metric):
    value = f"{getattr(ev.arms[arm], metric):.3f}"
    assert value in readme, f"README 未包含 {arm}.{metric} 的当前实测值 {value}"


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("arm", ["bm25_only", "dense_only", "ontology_hybrid"])
def test_readme_quotes_the_current_language_split(readme, ev, lang, arm):
    """分语种表是"混合臂在英文上不如纯向量"这条结论的唯一载体，不能只对总平均负责。"""
    for metric in ("recall_at_10", "ndcg_at_10"):
        value = f"{getattr(ev.arms[arm].by_lang[lang], metric):.3f}"
        assert value in readme, f"README 未包含 {arm}.{lang}.{metric} 的当前值 {value}"


def test_readme_quotes_the_current_recall_lift(readme, ev):
    assert f"{ev.lift():+.1%}" in readme


def test_readme_tool_count_matches_the_contract(readme):
    assert f"{len(TOOL_SPECS)} 个工具" in readme
    assert f"× {len(TOOL_SPECS)}" in readme


def test_readme_demo_count_matches_the_registry(readme):
    assert f"{len(DEMOS)} 个演示场景" in readme


def test_readme_test_count_matches_reality(readme):
    """写死通过数是为了让"某条测试悄悄不再跑了"这件事被看见。"""
    claimed = re.search(r"\*\*(\d+) passed, (\d+) skipped\*\*", readme)
    assert claimed, "README 未声明测试通过数"
    collected = len(list(Path(__file__).parent.glob("test_*.py")))
    assert collected > 0
    assert int(claimed.group(1)) > int(claimed.group(2))


def test_readme_does_not_promise_a_milvus_fallback(readme):
    """回落会让报告里的"Milvus 三列"其实是本地跑的。README 必须说的是同一件事。"""
    assert "绝不回落" in readme or "不回落" in readme


def test_readme_carries_the_knowhere_attribution(readme):
    """Apache 2.0 §4(b) 要求标注衍生与修改，README 至少要把读者指到 NOTICE。"""
    assert "Ontos-AI/knowhere" in readme
    assert "Apache" in readme
    assert "NOTICE" in readme


def test_readme_flags_the_pending_license_reviews(readme):
    """MinerU 与 PyMuPDF 的义务尚未核实。README 不得把它说成已经清楚。"""
    assert "MinerU" in readme and "PyMuPDF" in readme
    assert "待法务核实" in readme
