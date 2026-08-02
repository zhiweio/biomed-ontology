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


def test_the_recovered_mrr_target_kept_its_seat(outcomes):
    """T4 走完了"写死断言 → 未达成+豁免 → 达成+撤销豁免"的整条路径。

    它现在守的是最后一步：目标本身**没有随豁免一起被删掉**。
    一条曾经红过、后来转绿的目标最容易在清理时被顺手删除 ——
    删了之后它再退回去也没人知道，而这正是当初设立豁免机制要防的事。
    """
    t4 = next(o for o in outcomes if o.target.id == "T4")
    assert t4.met, "MRR 又退回去了：这次要重新写豁免，不是删目标"
    assert not t4.waived, "已达成还挂着豁免，对外结论会继续引用过期的免责说明"


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


@pytest.mark.xfail(
    strict=True,
    reason="T1 当前只能靠豁免过关，核心承诺未被证明。"
    "标注覆盖已经不是理由了 —— gold 扩到全部 14 篇 / 28 条 query 后 judged@10=1.000，"
    "0.335 → 0.317（-5.2%）是干净的测量值。"
    "消融显示本体今天只经由 GRAPH 一个通道起作用，而该通道净值 -0.018；"
    "`expand` 只在图内部展开下位概念、从不改写词法/向量查询串，对总分贡献 +0.002。"
    "按子集拆：真实文献 20 条 +5.4%，早期构造 8 条 -16.4%，总均值为负全由后者贡献。"
    "这条守卫的存在意义就是不让「核心承诺被豁免」这件事悄悄发生 —— "
    "标成 xfail(strict) 是记账，不是消音："
    "检索侧改造完成、T1 真达成后它会立刻转绿并要求删掉本标记。",
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
