"""指标目标与豁免。

**核心设计：一条达不到的目标不能被删掉，只能被署名豁免。**

没有这个机制时，红掉的断言只有两条出路 —— 删掉，或者调低阈值。
两条都会让对外结论悄悄和事实脱节，且事后无从追溯是哪次提交脱的。

反向绊线一样重要：目标已达成却仍挂着豁免，也判为失败。
否则免责声明会永远留在文档里，把一个早已解决的问题说成尚未解决。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.eval import RetrievalEval

__all__ = [
    "MetricTarget",
    "TargetOutcome",
    "check_targets",
    "load_targets",
    "render_outcomes",
]

_COMPARISONS = {"relative_gain", "absolute_gain", "not_worse", "at_least"}


@dataclass(frozen=True)
class MetricTarget:
    id: str
    metric: str
    arm: str
    comparison: str
    threshold: float
    baseline_arm: str | None = None
    lang: str | None = None
    rationale: str = ""
    waiver: str = ""
    waiver_owner: str = ""
    waiver_review_by: str = ""

    @property
    def waived(self) -> bool:
        """豁免必须同时有理由和署名人。只写理由等于没人为它负责。"""
        return bool(self.waiver.strip()) and bool(self.waiver_owner.strip())


@dataclass(frozen=True)
class TargetOutcome:
    target: MetricTarget
    actual: float
    baseline: float | None
    observed: float
    met: bool
    unavailable: bool = False

    @property
    def waived(self) -> bool:
        return self.target.waived

    @property
    def stale_waiver(self) -> bool:
        """已达成却还挂着豁免 —— 对外结论仍在引用一条过期的免责说明。"""
        return self.met and self.target.waived

    def explain(self) -> str:
        if self.unavailable:
            return f"{self.target.id} 未运行：{self.target.arm} 臂不可用"
        verdict = "达成" if self.met else ("未达成（已豁免）" if self.waived else "未达成")
        base = f"（基线 {self.baseline:.3f}）" if self.baseline is not None else ""
        return (
            f"{self.target.id} {verdict}｜{self.target.arm}.{self.target.metric}"
            f"{'@' + self.target.lang if self.target.lang else ''} = {self.actual:.3f}{base}"
            f"｜{self.target.comparison} 阈值 {self.target.threshold:+.3f}"
            f"｜实测 {self.observed:+.3f}"
        )


def load_targets(path: Path | None = None) -> list[MetricTarget]:
    root = path or Path(__file__).resolve().parents[3] / "data" / "gold" / "targets.yaml"
    raw: dict[str, Any] = yaml.safe_load(root.read_text(encoding="utf-8"))
    targets = []
    for item in raw.get("targets") or []:
        comparison = item["comparison"]
        if comparison not in _COMPARISONS:
            raise ValueError(f"未知比较方式 {comparison!r}，可选：{sorted(_COMPARISONS)}")
        targets.append(MetricTarget(**item))
    return targets


def check_targets(
    ev: RetrievalEval, targets: list[MetricTarget] | None = None
) -> list[TargetOutcome]:
    outcomes = []
    for target in targets if targets is not None else load_targets():
        if target.arm not in ev.arms or (
            target.baseline_arm and target.baseline_arm not in ev.arms
        ):
            outcomes.append(TargetOutcome(target, 0.0, None, 0.0, met=False, unavailable=True))
            continue

        actual = _read(ev, target.arm, target.metric, target.lang)
        baseline = (
            _read(ev, target.baseline_arm, target.metric, target.lang)
            if target.baseline_arm
            else None
        )
        observed, met = _compare(target, actual, baseline)
        outcomes.append(TargetOutcome(target, actual, baseline, observed, met))
    return outcomes


def _read(ev: RetrievalEval, arm: str, metric: str, lang: str | None) -> float:
    result = ev.arms[arm]
    if lang is not None:
        sub = result.by_lang.get(lang)
        if sub is None:
            return 0.0
        result = sub
    return float(getattr(result, metric))


def _compare(target: MetricTarget, actual: float, baseline: float | None) -> tuple[float, bool]:
    if target.comparison == "at_least":
        return actual, actual >= target.threshold
    if baseline is None:
        raise ValueError(f"{target.id} 的 {target.comparison} 需要 baseline_arm")
    if target.comparison == "relative_gain":
        observed = (actual - baseline) / baseline if baseline else float("inf")
    else:  # absolute_gain / not_worse 都是绝对差
        observed = actual - baseline
    return observed, observed >= target.threshold


def render_outcomes(outcomes: list[TargetOutcome]) -> str:
    lines = ["指标目标"]
    for o in outcomes:
        lines.append(f"  {o.explain()}")
        if o.stale_waiver:
            lines.append(f"    ⚠ {o.target.id} 已达成但豁免仍在，请撤销并同步对外结论")
        elif not o.met and o.waived:
            first = o.target.waiver.strip().splitlines()[0]
            lines.append(
                f"    豁免人 {o.target.waiver_owner}｜复审时点 {o.target.waiver_review_by}"
            )
            lines.append(f"    理由 {first}")
    return "\n".join(lines)
