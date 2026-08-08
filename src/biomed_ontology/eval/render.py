"""Gold Eval 的 Rich 终端渲染（对齐 `hmd foundation golden` / `hmd demo`）。"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from biomed_ontology.eval import ONTOLOGY_PROBES, SAPBERT_DELTA, VISUAL_BIO_DELTA, VISUAL_DELTA

if TYPE_CHECKING:
    from biomed_ontology.eval import NormalizationEval, RetrievalEval
    from biomed_ontology.eval.targets import TargetOutcome

__all__ = [
    "render_eval",
    "render_eval_compact",
    "summary_json",
]


def render_eval(
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """分步展示归一化 → 检索消融 → 指标目标。"""
    out = console or Console()
    targets_ok = _targets_ok(outcomes)
    citation_ok = all(a.citation_fidelity >= 1.0 for a in retrieval.arms.values())
    all_ok = targets_ok and citation_ok and not retrieval.unavailable

    out.print()
    out.print(
        _header_panel(
            norm=norm,
            retrieval=retrieval,
            outcomes=outcomes,
            all_ok=all_ok,
        )
    )
    out.print()
    out.print(_trace_panel(norm=norm, retrieval=retrieval, outcomes=outcomes))
    out.print()

    if verbose:
        out.print(Rule("[dim]① Normalization[/]", style="dim"))
        out.print(_normalization_panel(norm))
        out.print()

        out.print(Rule("[dim]② Retrieval · Arms[/]", style="dim"))
        out.print(_arms_panel(retrieval, key=None, title="全部 query"))
        out.print()
        if _has_probe_slice(retrieval):
            out.print(
                _arms_panel(
                    retrieval,
                    probes=ONTOLOGY_PROBES,
                    title="本体敏感探针（bridge_zh + alias，主 KPI）",
                )
            )
            out.print()

        out.print(Rule("[dim]③ Diagnostics[/]", style="dim"))
        out.print(_diagnostics_panel(retrieval))
        out.print()

        out.print(Rule("[dim]④ Targets[/]", style="dim"))
        out.print(_targets_panel(outcomes))
        out.print()

    out.print(_footer(norm=norm, retrieval=retrieval, outcomes=outcomes, all_ok=all_ok))
    out.print()


def render_eval_compact(
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
    *,
    console: Console | None = None,
) -> None:
    """仅 Header + Trace + Footer。"""
    render_eval(norm, retrieval, outcomes, console=console, verbose=False)


def summary_json(
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
) -> str:
    """机器可读摘要（脚本 / CI）。"""
    payload: dict[str, Any] = {
        "normalization": {
            "accuracy": norm.accuracy,
            "correct": norm.correct,
            "total": norm.total,
            "by_entity_type": {
                k: {"correct": c, "total": n} for k, (c, n) in sorted(norm.by_entity_type.items())
            },
            "failures": norm.failures[:20],
        },
        "retrieval": {
            "embedder": retrieval.embedder,
            "reranker": retrieval.reranker,
            "baseline": retrieval.baseline,
            "target": retrieval.target,
            "unavailable": retrieval.unavailable,
            "arms": {
                name: {
                    "label": arm.label,
                    "recall_at_10": arm.recall_at_10,
                    "precision_at_5": arm.precision_at_5,
                    "ndcg_at_10": arm.ndcg_at_10,
                    "mrr": arm.mrr,
                    "map_score": arm.map_score,
                    "citation_fidelity": arm.citation_fidelity,
                    "query_count": arm.query_count,
                }
                for name, arm in retrieval.arms.items()
            },
        },
        "targets": [
            {
                "id": o.target.id,
                "met": o.met,
                "waived": o.waived,
                "stale_waiver": o.stale_waiver,
                "unavailable": o.unavailable,
                "actual": o.actual,
                "observed": o.observed,
                "threshold": o.target.threshold,
                "comparison": o.target.comparison,
            }
            for o in outcomes
        ],
        "ok": _targets_ok(outcomes)
        and all(a.citation_fidelity >= 1.0 for a in retrieval.arms.values())
        and not retrieval.unavailable,
    }
    if _has_probe_slice(retrieval):
        payload["retrieval"]["ontology_probe_ndcg_gain"] = retrieval.absolute_gain(
            probes=ONTOLOGY_PROBES
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- panels


def _header_panel(
    *,
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
    all_ok: bool,
) -> Panel:
    title = Text()
    title.append("Gold Eval", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("normalization + retrieval + targets", style="dim cyan")

    body = Text()
    body.append(f"{norm.accuracy:.1%}", style="bold bright_cyan")
    body.append("  norm  ·  ", style="dim")
    if _has_probe_slice(retrieval):
        gain = retrieval.absolute_gain(probes=ONTOLOGY_PROBES)
        body.append(f"{gain:+.3f}", style="bold bright_cyan" if gain >= 0 else "bold yellow")
        body.append("  probe nDCG  ·  ", style="dim")
    met = sum(1 for o in outcomes if o.met or o.waived)
    body.append(f"{met}/{len(outcomes)}", style="bold bright_cyan" if all_ok else "bold yellow")
    body.append("  targets\n", style="dim")

    body.append("embedder  ", style="dim")
    body.append(escape(retrieval.embedder or "—"), style="white")
    body.append("  ·  reranker  ", style="dim")
    body.append(escape(retrieval.reranker or "关闭"), style="white")
    body.append("\n")
    body.append("status  ", style="dim")
    if all_ok:
        body.append("OK", style="bold green")
        body.append("  ·  targets met, citation fidelity intact", style="dim")
    else:
        body.append("CHECK", style="bold yellow")
        body.append("  ·  inspect Trace / Targets before shipping numbers", style="dim")

    border = "bright_green" if all_ok else "yellow"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _trace_panel(
    *,
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
) -> Panel:
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("ok", width=2)
    table.add_column("step", style="dim", width=3)
    table.add_column("name", style="bold", width=14)
    table.add_column("detail")

    fail_n = len(norm.failures)
    norm_ok = fail_n == 0
    table.add_row(
        "[green]✓[/]" if norm_ok else "[yellow]![/]",
        "1",
        "Normalize",
        f"[bold]{norm.accuracy:.1%}[/]  ({norm.correct}/{norm.total})"
        + (f"  [red]{fail_n} fail[/]" if fail_n else ""),
    )

    if _has_probe_slice(retrieval):
        gain = retrieval.absolute_gain(probes=ONTOLOGY_PROBES)
        detail = f"probe nDCG gain [bold]{gain:+.3f}[/]  ·  arms [bold]{len(retrieval.arms)}[/]"
    else:
        lift = retrieval.lift() if retrieval.baseline in retrieval.arms else float("nan")
        lift_s = f"{lift:+.1%}" if not math.isnan(lift) and math.isfinite(lift) else "—"
        detail = f"lift Recall@10 [bold]{lift_s}[/]  ·  arms [bold]{len(retrieval.arms)}[/]"
    if retrieval.unavailable:
        detail += f"  [yellow]{len(retrieval.unavailable)} unavailable[/]"
    table.add_row("[green]✓[/]", "2", "Retrieval", detail)

    unmet = [o for o in outcomes if not o.met and not o.waived]
    stale = [o for o in outcomes if o.stale_waiver]
    if unmet or stale:
        mark = "[red]✗[/]"
        bits = []
        if unmet:
            bits.append(f"[red]{len(unmet)} unmet[/]")
        if stale:
            bits.append(f"[yellow]{len(stale)} stale waiver[/]")
        t_detail = "  ".join(bits)
    else:
        mark = "[green]✓[/]"
        waived = sum(1 for o in outcomes if o.waived and not o.met)
        t_detail = f"[bold]{sum(o.met for o in outcomes)}/{len(outcomes)}[/] met"
        if waived:
            t_detail += f"  [dim]{waived} waived[/]"
    table.add_row(mark, "3", "Targets", t_detail)

    return Panel(table, title="[bold]Trace[/]", border_style="dim", box=box.SIMPLE)


def _normalization_panel(norm: NormalizationEval) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()
    grid.add_row("accuracy", f"[bold]{norm.accuracy:.1%}[/]  ({norm.correct}/{norm.total})")
    for et, (c, n) in sorted(norm.by_entity_type.items()):
        acc = c / n if n else 0.0
        grid.add_row(escape(et), f"{acc:>6.1%}  ({c}/{n})")
    if norm.ambiguous_total:
        acc = norm.ambiguous_correct / norm.ambiguous_total
        grid.add_row(
            "消歧",
            f"{acc:>6.1%}  ({norm.ambiguous_correct}/{norm.ambiguous_total})",
        )

    parts: list[RenderableType] = [grid]
    if norm.failures:
        fail = Table(box=box.SIMPLE_HEAD, show_header=True, pad_edge=False, border_style="dim")
        fail.add_column("text", ratio=2, overflow="fold")
        fail.add_column("expect", style="green", ratio=1)
        fail.add_column("got", style="red", ratio=1)
        for f in norm.failures[:10]:
            fail.add_row(
                escape(repr(f["text"])),
                escape(str(f["expect"])),
                escape(str(f["got"])),
            )
        parts.extend([Text(""), fail])

    border = "green" if not norm.failures else "yellow"
    return Panel(Group(*parts), title="Normalization", border_style=border, box=box.ROUNDED)


def _arms_panel(
    retrieval: RetrievalEval,
    *,
    key: str | None = None,
    probes: tuple[str, ...] | None = None,
    title: str,
    axis: str = "by_lang",
) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=False,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("臂", min_width=18, no_wrap=True)
    for col, justify in (
        ("Recall@10", "right"),
        ("P@5", "right"),
        ("nDCG@10", "right"),
        ("MRR", "right"),
        ("MAP", "right"),
        ("P50ms", "right"),
        ("n", "right"),
    ):
        table.add_column(col, justify=justify)

    for arm_name, arm in retrieval.arms.items():
        if probes:
            r = retrieval._result(arm_name, None, probes=probes)
        elif key is None:
            r = arm
        else:
            r = getattr(arm, axis).get(key)
            if r is None:
                continue
        table.add_row(
            escape(arm.label),
            f"{r.recall_at_10:.3f}",
            f"{r.precision_at_5:.3f}",
            f"{r.ndcg_at_10:.3f}",
            f"{r.mrr:.3f}",
            f"{r.map_score:.3f}",
            f"{r.latency_p50_ms:.1f}",
            str(r.query_count),
        )

    return Panel(table, title=escape(title), border_style="cyan", box=box.ROUNDED)


def _diagnostics_panel(retrieval: RetrievalEval) -> Panel:
    lines: list[str] = []

    if _has_probe_slice(retrieval):
        gain = retrieval.absolute_gain(probes=ONTOLOGY_PROBES)
        lines.append(f"本体敏感探针 nDCG@10 绝对增益：[bold]{gain:+.3f}[/]（主 KPI）")
    if retrieval.baseline in retrieval.arms and retrieval.target in retrieval.arms:
        lift = retrieval.lift()
        lift_s = f"{lift:+.1%}" if math.isfinite(lift) else "inf"
        lines.append(f"全量 Recall@10 相对提升：{lift_s}（诊断，含图像/对照稀释）")

    if retrieval.baseline in retrieval.arms and retrieval.target in retrieval.arms:
        lines.append("")
        lines.append(
            f"[bold]配对显著性[/]（{escape(retrieval.target)} − {escape(retrieval.baseline)}）"
        )
        if _has_probe_slice(retrieval):
            lines.append("  本体敏感探针（主 KPI）：")
            for metric in ("ndcg_at_10", "recall_at_10", "precision_at_5"):
                sig = retrieval.significance(metric, probes=ONTOLOGY_PROBES)
                lines.append(f"    {metric:<14} {escape(sig.render())}")
        for metric in ("ndcg_at_10", "recall_at_10", "precision_at_5"):
            sig = retrieval.significance(metric)
            lines.append(f"  全量 {metric:<11} {escape(sig.render())}")

    hi, lo = SAPBERT_DELTA
    if hi in retrieval.arms and lo in retrieval.arms:
        lines.append("")
        lines.append(
            f"SapBERT 净值（三列 − 双列，rerank={escape(retrieval.reranker or '关闭')}，"
            f"embedder={escape(retrieval.embedder or '?')}）："
        )
        for lang in [None, *sorted({lg for a in retrieval.arms.values() for lg in a.by_lang})]:
            tag = lang or "全部"
            lines.append(f"  {tag:<6} Recall@10 {retrieval.delta(lang=lang):+.3f}")
        if not _has_sapbert(retrieval.embedder):
            lines.append(
                f"  [yellow]⚠[/] embedder={escape(retrieval.embedder or '?')} 并未加载 SapBERT，"
                "净值只验证链路贯通"
            )

    for pair, title in (
        (VISUAL_DELTA, "视觉列净值（四列 − 三列）："),
        (VISUAL_BIO_DELTA, "生医视觉列净值（五列 − 四列）："),
    ):
        vhi, vlo = pair
        if vhi not in retrieval.arms or vlo not in retrieval.arms:
            continue
        lines.append("")
        lines.append(title)
        for lang in [None, *sorted({lg for a in retrieval.arms.values() for lg in a.by_lang})]:
            tag = lang or "全部"
            lines.append(f"  {tag:<6} Recall@10 {retrieval.delta(lang=lang, pair=pair):+.3f}")

    broken = {
        a.label: a.citation_fidelity for a in retrieval.arms.values() if a.citation_fidelity < 1
    }
    lines.append("")
    if broken:
        lines.append("[bold red]引用忠实度破损[/]（硬约束，不得低于 1.000）：")
        lines.extend(f"  {escape(label):<24} {v:.3f}" for label, v in sorted(broken.items()))
    else:
        lines.append("引用忠实度：全部臂 [green]1.000[/]（无造引用）")

    if retrieval.unavailable:
        lines.append("")
        lines.append("[yellow]未运行的臂[/]（后端不可达，非结果）：")
        lines.extend(
            f"  {escape(arm):<24} {escape(why)}"
            for arm, why in sorted(retrieval.unavailable.items())
        )

    worst = min((a.judged_at_10 for a in retrieval.arms.values()), default=1.0)
    if worst < 0.9:
        lines.append("")
        lines.append(
            f"[yellow]⚠[/] 标注覆盖不足：最差臂前十仅 {worst:.0%} 被 gold 判定过（指标为下界）"
        )

    body = Text.from_markup("\n".join(lines) if lines else "[dim]no diagnostics[/]")
    return Panel(body, title="Diagnostics", border_style="magenta", box=box.ROUNDED)


def _targets_panel(outcomes: list[TargetOutcome]) -> Panel:
    if not outcomes:
        return Panel("[dim]no targets[/]", border_style="dim", box=box.ROUNDED)

    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("ok", width=2)
    table.add_column("id", style="bold", min_width=16)
    table.add_column("verdict", width=12)
    table.add_column("detail", ratio=3, overflow="fold")

    notes: list[str] = []
    for o in outcomes:
        if o.unavailable:
            mark, verdict, style = "[yellow]—[/]", "unavailable", "yellow"
        elif o.stale_waiver:
            mark, verdict, style = "[yellow]![/]", "stale", "yellow"
        elif o.met:
            mark, verdict, style = "[green]✓[/]", "met", "green"
        elif o.waived:
            mark, verdict, style = "[yellow]~[/]", "waived", "yellow"
        else:
            mark, verdict, style = "[red]✗[/]", "unmet", "red"

        scope = ""
        if o.target.probes:
            scope = "@" + "+".join(o.target.probes)
        elif o.target.lang:
            scope = "@" + o.target.lang
        detail = (
            f"{escape(o.target.arm)}.{escape(o.target.metric)}{escape(scope)} "
            f"= {o.actual:.3f}  ·  {escape(o.target.comparison)} "
            f"{o.target.threshold:+.3f}  ·  实测 {o.observed:+.3f}"
        )
        table.add_row(mark, escape(o.target.id), f"[{style}]{verdict}[/]", detail)

        if o.stale_waiver:
            notes.append(f"[yellow]⚠[/] {escape(o.target.id)} 已达成但豁免仍在，请撤销")
        elif not o.met and o.waived:
            first = o.target.waiver.strip().splitlines()[0] if o.target.waiver.strip() else ""
            notes.append(
                f"[dim]{escape(o.target.id)}[/]  豁免人 {escape(o.target.waiver_owner)}｜"
                f"复审 {escape(o.target.waiver_review_by)}｜{escape(first)}"
            )

    body: RenderableType = table
    if notes:
        body = Group(table, Text(""), Text.from_markup("\n".join(notes)))

    ok = _targets_ok(outcomes)
    return Panel(
        body,
        title="Targets",
        border_style="bright_green" if ok else "red",
        box=box.ROUNDED,
    )


def _footer(
    *,
    norm: NormalizationEval,
    retrieval: RetrievalEval,
    outcomes: list[TargetOutcome],
    all_ok: bool,
) -> Panel:
    text = Text()
    if all_ok:
        text.append("✓ ", style="bold green")
        text.append("Gold eval ready", style="bold")
    else:
        text.append("✗ ", style="bold red")
        text.append("Gold eval needs attention", style="bold")

    met = sum(1 for o in outcomes if o.met or o.waived)
    text.append(
        f"  ·  norm={norm.accuracy:.1%}  ·  targets={met}/{len(outcomes)}  ·  "
        f"arms={len(retrieval.arms)}",
        style="dim",
    )
    text.append("\n")
    text.append("next  ", style="dim")
    text.append("hmd demo", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd foundation golden --candidate HMPL-504", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd serve --mcp", style="cyan")

    border = "bright_green" if all_ok else "red"
    return Panel(text, border_style=border, box=box.ROUNDED)


# ---------------------------------------------------------------- helpers


def _targets_ok(outcomes: list[TargetOutcome]) -> bool:
    return all((o.met or o.waived) and not o.stale_waiver for o in outcomes) if outcomes else True


def _has_probe_slice(retrieval: RetrievalEval) -> bool:
    return any(ONTOLOGY_PROBES[0] in a.by_probe for a in retrieval.arms.values())


def _has_sapbert(name: str | None) -> bool:
    lowered = (name or "").lower()
    return "sapbert" in lowered or lowered in {"dual", "multimodal"}
