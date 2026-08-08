"""Demo 场景的 Rich 终端渲染（对齐 `hmd foundation golden` 风格）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from biomed_ontology.demo import DemoResult

__all__ = ["render_demo_results", "render_demo_results_compact"]


def render_demo_results(
    results: list[DemoResult],
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """分场景展示基座能力验收（别名 / 扩展 / 许可 / Citationware …）。"""
    out = console or Console()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    all_ok = passed == total and total > 0

    out.print()
    out.print(_header_panel(passed=passed, total=total, all_ok=all_ok))
    out.print()
    out.print(_trace_panel(results))
    out.print()

    if verbose:
        for r in results:
            out.print(Rule(f"[dim]{escape(r.demo_id)} · {escape(r.title)}[/]", style="dim"))
            out.print(_demo_panel(r))
            out.print()

    out.print(_footer(passed=passed, total=total, all_ok=all_ok))
    out.print()


def render_demo_results_compact(
    results: list[DemoResult],
    *,
    console: Console | None = None,
) -> None:
    """仅 Trace 摘要。"""
    render_demo_results(results, console=console, verbose=False)


def _header_panel(*, passed: int, total: int, all_ok: bool) -> Panel:
    title = Text()
    title.append("Semantic Tools Demo", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("capability assertions", style="dim cyan")

    body = Text()
    body.append(f"{passed}/{total}", style="bold bright_cyan" if all_ok else "bold yellow")
    body.append("  scenarios passed\n", style="dim")
    if all_ok:
        body.append("status  ", style="dim")
        body.append("OK", style="bold green")
        body.append("  ·  falsifiable claims, not print-only", style="dim")
    else:
        body.append("status  ", style="dim")
        body.append("FAILED", style="bold red")
        body.append("  ·  fix failing assertions before ship", style="dim")

    border = "bright_green" if all_ok else "red"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _trace_panel(results: list[DemoResult]) -> Panel:
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("ok", width=2)
    table.add_column("id", style="bold", width=4)
    table.add_column("title", style="bold", min_width=12)
    table.add_column("claim", overflow="fold")

    for r in results:
        mark = "[green]✓[/]" if r.passed else "[red]✗[/]"
        claim = escape(r.claim)
        claim = f"[red]{claim}[/]" if not r.passed else f"[dim]{claim}[/]"
        table.add_row(mark, escape(r.demo_id), escape(r.title), claim)

    return Panel(table, title="[bold]Trace[/]", border_style="dim", box=box.SIMPLE)


def _demo_panel(result: DemoResult) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=8)
    grid.add_column()

    status = Text("PASS", style="bold green") if result.passed else Text("FAIL", style="bold red")
    grid.add_row("status", status)
    grid.add_row("claim", escape(result.claim))

    detail = Text()
    if result.lines:
        for i, line in enumerate(result.lines):
            if i:
                detail.append("\n")
            detail.append(escape(line), style="white" if result.passed else "yellow")
    else:
        detail.append("—", style="dim")

    body = Group(grid, Text(""), detail)
    border = "green" if result.passed else "red"
    title = f"[bold]{escape(result.demo_id)}[/]  {escape(result.title)}"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED)


def _footer(*, passed: int, total: int, all_ok: bool) -> Panel:
    text = Text()
    if all_ok:
        text.append("✓ ", style="bold green")
        text.append("Tool surface ready", style="bold")
    else:
        text.append("✗ ", style="bold red")
        text.append("Tool surface regressions", style="bold")
    text.append(
        f"  ·  passed={passed}/{total}  ·  next  ",
        style="dim",
    )
    text.append("hmd serve --mcp", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd eval --entitlements MOCK_LICENSED", style="cyan")
    border = "bright_green" if all_ok else "red"
    return Panel(text, border_style=border, box=box.ROUNDED)
