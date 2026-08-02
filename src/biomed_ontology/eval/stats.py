"""臂间差异的显著性：配对自助 CI + 配对置换检验。

n=28 上，±0.02 的差值落在抖动范围内。没有这一层，"本体增强 +0.013"
和"本体增强什么也没做"在报表上长得一模一样，而读的人只能凭符号下结论。

两个统计量分工不同，都要报：
- **95% CI** 来自配对自助（bootstrap）—— 回答"这个差值可能有多大/多小"；
- **p 值** 来自配对置换（randomization）—— 回答"这个差值有多容易由巧合产生"。
IR 评测里置换检验比 t 检验更稳妥：它不假设差值服从正态，而 28 条 query 的
per-query 差值分布通常是长尾且带一堆 0（两臂给出同一批命中）。

**配对**是关键：两臂跑的是同一批 query，query 本身的难度差异是最大的方差来源，
配对能整块消掉它。独立两样本检验在这个规模上基本什么都测不出来。

随机数固定种子：README 里的数字必须能被重跑复现，一个每次都不一样的 p 值
会让"数字有没有变"这件事无法判定。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = ["Significance", "paired_significance"]


@dataclass(frozen=True)
class Significance:
    """一次配对比较的结果。`delta` 是 target − baseline 的均值。"""

    delta: float
    ci_low: float
    ci_high: float
    p_value: float
    n: int

    @property
    def significant(self) -> bool:
        """p < 0.05 且 CI 不跨零。两条都要满足 —— 只看 p 值会把
        "显著但幅度可能是 +0.001" 报成一个胜利。"""
        return self.p_value < 0.05 and (self.ci_low > 0) == (self.ci_high > 0)

    def render(self) -> str:
        star = "" if self.significant else "  (n.s.)"
        return (
            f"{self.delta:+.3f}  95% CI [{self.ci_low:+.3f}, {self.ci_high:+.3f}]  "
            f"p={self.p_value:.3f}  n={self.n}{star}"
        )


def paired_significance(
    target: dict[str, float],
    baseline: dict[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 20240501,
) -> Significance:
    """对两臂的 per-query 分数做配对比较。

    只取两臂都跑过的 query（键的交集）。臂间 query 集合不同时强行比较
    —— 例如把只在图像意图上评分的视觉臂和全量臂放在一起 —— 得到的差值
    没有任何含义，所以这里按交集截断，并把实际参与的条数报在 `n` 上。
    """
    qids = sorted(set(target) & set(baseline))
    diffs = [target[q] - baseline[q] for q in qids]
    n = len(diffs)
    if n == 0:
        return Significance(0.0, 0.0, 0.0, 1.0, 0)

    observed = sum(diffs) / n
    rng = random.Random(seed)

    # 配对自助：对 query 下标有放回重采样，每次重算差值均值。
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    ci_low = means[int(0.025 * (resamples - 1))]
    ci_high = means[int(0.975 * (resamples - 1))]

    # 配对置换：零假设下"哪一臂更好"是随机的，等价于逐条随机翻转差值符号。
    # 全部差值为 0 时（两臂给出完全相同的排序）p 记为 1.0，
    # 否则会得到一个 0.000 —— 那是"两臂毫无差别"被报成"差别极显著"。
    if all(d == 0.0 for d in diffs):
        return Significance(0.0, 0.0, 0.0, 1.0, n)
    extreme = 0
    target_abs = abs(observed)
    for _ in range(resamples):
        total = 0.0
        for d in diffs:
            total += d if rng.random() < 0.5 else -d
        if abs(total / n) >= target_abs:
            extreme += 1
    # +1 平滑：置换检验的 p 不该出现 0.000。真实含义是"在 1 万次重排里一次都没
    # 出现过这么极端的值"，那是 p < 1e-4，不是 p = 0。
    p_value = (extreme + 1) / (resamples + 1)

    return Significance(observed, ci_low, ci_high, p_value, n)
