"""指标目标与豁免机制。

这套机制的价值全在两条绊线上：
- 未达成 + 无豁免 → 失败（否则目标形同虚设）
- 已达成 + 有豁免 → 也失败（否则免责声明会永远留在对外文档里）
"""

from __future__ import annotations

import pytest

from biomed_ontology.eval import eval_retrieval
from biomed_ontology.eval.targets import (
    MetricTarget,
    check_targets,
    load_targets,
    render_outcomes,
)
from biomed_ontology.pipeline import build_knowledge_base

LICENSED = frozenset({"MOCK_LICENSED"})


@pytest.fixture(scope="module")
def outcomes():
    return check_targets(eval_retrieval(build_knowledge_base(), entitlements=LICENSED))


# ------------------------------------------------------------------ 主绊线


def test_every_target_is_met_or_explicitly_waived(outcomes):
    """整套机制的落点：要么达成，要么有人署名说明为什么没达成。"""
    unmet = [o for o in outcomes if not o.met and not o.waived and not o.unavailable]
    assert not unmet, render_outcomes(outcomes)


def test_no_stale_waivers(outcomes):
    """反向绊线：目标已达成却还挂着豁免 —— 对外结论在引用过期的免责说明。"""
    stale = [o for o in outcomes if o.stale_waiver]
    assert not stale, render_outcomes(outcomes)


def test_the_known_mrr_regression_is_waived_not_deleted(outcomes):
    """MRR 那条曾是写死的 `<= 0` 断言。它现在必须以"未达成 + 带理由"的形态存在。"""
    t4 = next(o for o in outcomes if o.target.id == "T4")
    assert not t4.met, "MRR 回升了，请撤销 T4 的豁免并同步更新对外结论"
    assert t4.waived
    assert "样本量" in t4.target.waiver


def test_waiver_text_quotes_the_current_numbers(outcomes):
    """豁免里写的数字必须还是真的。

    数字写错的豁免比没有豁免更糟：它让读者以为有人核对过，
    而实际上那串数字来自某个早已改掉的版本。
    """
    for o in outcomes:
        if not o.waived:
            continue
        for value in (o.actual, o.baseline):
            if value is None:
                continue
            assert f"{value:.3f}" in o.target.waiver, (
                f"{o.target.id} 的豁免文本没有引用当前实测值 {value:.3f}；"
                f"数字变了就必须重写理由，而不是留着旧的"
            )


def test_recall_target_is_actually_met(outcomes):
    """不能全靠豁免过关 —— 核心承诺必须是真达成的。"""
    t1 = next(o for o in outcomes if o.target.id == "T1")
    assert t1.met and not t1.target.waived, t1.explain()


# ------------------------------------------------------------------ 豁免形态


def test_waiver_without_an_owner_does_not_count():
    """只写理由不署名，等于没人为它负责。"""
    t = MetricTarget(
        id="X", metric="mrr", arm="a", comparison="at_least", threshold=1.0, waiver="样本量太小"
    )
    assert not t.waived


def test_owner_without_a_reason_does_not_count():
    t = MetricTarget(
        id="X", metric="mrr", arm="a", comparison="at_least", threshold=1.0, waiver_owner="张三"
    )
    assert not t.waived


def test_every_waiver_in_the_repo_names_a_review_point():
    """豁免不写复审时点就会变成永久豁免，那和删掉目标没有区别。"""
    for t in load_targets():
        if t.waived:
            assert t.waiver_review_by.strip(), f"{t.id} 的豁免没有复审时点"


def test_every_target_states_why_it_exists():
    for t in load_targets():
        assert t.rationale.strip(), f"{t.id} 没写为什么要有这个目标"


# ------------------------------------------------------------------ 比较语义


@pytest.mark.parametrize(
    ("comparison", "actual", "baseline", "threshold", "expected"),
    [
        ("relative_gain", 1.10, 1.00, 0.10, True),
        ("relative_gain", 1.05, 1.00, 0.10, False),
        ("not_worse", 0.99, 1.00, 0.0, False),
        ("not_worse", 1.00, 1.00, 0.0, True),
        ("absolute_gain", 1.20, 1.00, 0.15, True),
        ("at_least", 0.80, None, 0.75, True),
        ("at_least", 0.70, None, 0.75, False),
    ],
)
def test_comparison_semantics(comparison, actual, baseline, threshold, expected):
    from biomed_ontology.eval.targets import _compare

    target = MetricTarget(
        id="X",
        metric="m",
        arm="a",
        comparison=comparison,
        threshold=threshold,
        baseline_arm="b" if baseline is not None else None,
    )
    _, met = _compare(target, actual, baseline)
    assert met is expected


def test_unknown_comparison_fails_at_load_time(tmp_path):
    """比较方式打错字必须在加载时就炸，而不是静默算出一个错误结论。"""
    import yaml

    path = tmp_path / "targets.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0",
                "targets": [
                    {"id": "X", "metric": "mrr", "arm": "a", "comparison": "beter", "threshold": 0}
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未知比较方式"):
        load_targets(path)


def test_missing_arm_is_reported_as_unavailable_not_failed(kb=None):
    """臂没跑过，既不是达成也不是未达成。混为一谈会诬告一个没测的配置。"""
    ev = eval_retrieval(build_knowledge_base(), entitlements=LICENSED)
    target = MetricTarget(
        id="X",
        metric="recall_at_10",
        arm="milvus_hybrid_3col",
        comparison="at_least",
        threshold=0.5,
    )
    outcome = check_targets(ev, [target])[0]
    assert outcome.unavailable
    assert not outcome.met
    assert "未运行" in outcome.explain()
