"""Golden Path / World Model 的 Rich 终端渲染（coding-agent CLI 风格）。"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

__all__ = ["render_golden_path", "render_golden_path_compact"]

_TYPE_STYLE = {
    "PubMed": "cyan",
    "Patent": "magenta",
    "ELN": "green",
    "LIMS": "yellow",
    "manual": "dim",
}


def render_golden_path(
    result: dict[str, Any],
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """分步展示 resolve → entity → graph → evidence → assets。"""
    out = console or Console()
    if not result.get("ok"):
        out.print(
            Panel(
                escape(str(result.get("reason") or result)),
                title="[bold red]Golden Path FAILED[/]",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        return

    ctx = result.get("context") or {}
    entity = ctx.get("entity") or {}
    canonical = result.get("canonical_entity") or ctx.get("enterprise_id") or "?"
    query = _resolve_query(result)

    out.print()
    out.print(
        _header_panel(
            query=query,
            canonical=canonical,
            entity=entity,
            path=result.get("path"),
        )
    )
    out.print()
    out.print(_steps_panel(result))
    out.print()

    if verbose:
        out.print(Rule("[dim]① Resolve[/]", style="dim"))
        out.print(_resolve_panel(result))
        out.print()

        out.print(Rule("[dim]② Entity[/]", style="dim"))
        out.print(_entity_panel(entity))
        out.print()

        out.print(Rule("[dim]③ World Model Graph[/]", style="dim"))
        out.print(_graph_tree(ctx, canonical=canonical, entity=entity))
        out.print()

        out.print(Rule("[dim]④ Evidence · Citationware[/]", style="dim"))
        out.print(_evidence_table(ctx.get("evidence") or []))
        out.print()

        out.print(Rule("[dim]⑤ Enterprise Assets[/]", style="dim"))
        out.print(_assets_table(ctx.get("internal_assets") or []))
        out.print()

    out.print(_footer(ctx, canonical=canonical))
    out.print()


def render_golden_path_compact(result: dict[str, Any], *, console: Console | None = None) -> None:
    """仅步骤条 + 计数（旧行为的增强版）。"""
    render_golden_path(result, console=console, verbose=False)


def _resolve_query(result: dict[str, Any]) -> str:
    resolve = result.get("resolve") or {}
    return str(resolve.get("query") or result.get("query") or "")


def _header_panel(
    *,
    query: str,
    canonical: str,
    entity: dict[str, Any],
    path: str | None,
) -> Panel:
    label_en = entity.get("preferred_label_en") or ""
    label_zh = entity.get("preferred_label_zh") or ""
    kind = entity.get("entity_kind") or "Entity"
    aliases = entity.get("aliases") or []

    title = Text()
    title.append("Golden Path", style="bold bright_white")
    if path:
        title.append("  ·  ", style="dim")
        title.append(path, style="dim cyan")

    body = Text()
    if query:
        body.append(escape(query), style="bold yellow")
        body.append("  →  ", style="dim")
    body.append(escape(canonical), style="bold bright_cyan")
    body.append("\n")
    body.append(f"{kind}", style="green")
    if label_en:
        body.append("  ·  ", style="dim")
        body.append(escape(label_en), style="white")
    if label_zh:
        body.append("  ·  ", style="dim")
        body.append(escape(label_zh), style="white")
    if aliases:
        shown = ", ".join(escape(a) for a in aliases[:6])
        more = f" +{len(aliases) - 6}" if len(aliases) > 6 else ""
        body.append("\n")
        body.append("aliases  ", style="dim")
        body.append(shown + more, style="dim")

    return Panel(body, title=title, border_style="bright_blue", box=box.ROUNDED, padding=(1, 2))


def _steps_panel(result: dict[str, Any]) -> Panel:
    ctx = result.get("context") or {}
    hit = _primary_resolve_hit(result)
    method = (hit or {}).get("resolution_method") or "—"
    conf = (hit or {}).get("confidence")

    conf_s = f"  conf={conf:.2f}" if conf is not None else ""
    rows: list[tuple[str, str, str]] = [
        ("1", "Resolve", f"[cyan]{escape(str(method))}[/]{conf_s}"),
        ("2", "Targets", f"[bold]{len(ctx.get('targets') or [])}[/]"),
        ("3", "Diseases", f"[bold]{len(ctx.get('diseases') or [])}[/]"),
        ("4", "Evidence", f"[bold]{len(ctx.get('evidence') or [])}[/]  citationware"),
        ("5", "Assets", f"[bold]{len(ctx.get('internal_assets') or [])}[/]  ELN/LIMS"),
    ]

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("ok", width=2)
    table.add_column("step", style="dim", width=3)
    table.add_column("name", style="bold", width=10)
    table.add_column("detail")
    for step, name, detail in rows:
        table.add_row("[green]✓[/]", step, name, detail)

    return Panel(table, title="[bold]Trace[/]", border_style="dim", box=box.SIMPLE)


def _resolve_panel(result: dict[str, Any]) -> Panel:
    hit = _primary_resolve_hit(result)
    if not hit:
        return Panel("[dim]no resolve detail[/]", border_style="dim", box=box.ROUNDED)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    mention = hit.get("mention") or (result.get("resolve") or {}).get("query") or ""
    grid.add_row("mention", escape(str(mention)))
    canon = escape(str(hit.get("canonical_entity") or ""))
    grid.add_row("canonical", f"[bold cyan]{canon}[/]")
    grid.add_row("method", escape(str(hit.get("resolution_method") or "")))
    conf = hit.get("confidence")
    if conf is not None:
        grid.add_row("confidence", f"{float(conf):.2f}")
    kind = hit.get("entity_kind")
    if kind:
        grid.add_row("kind", escape(str(kind)))
    ext = hit.get("external_ids") or []
    if ext:
        grid.add_row("external", ", ".join(escape(x) for x in ext[:8]))

    return Panel(grid, title="Entity Resolution", border_style="cyan", box=box.ROUNDED)


def _entity_panel(entity: dict[str, Any]) -> Panel:
    if not entity:
        return Panel("[dim]missing entity[/]", border_style="red")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()
    grid.add_row("id", f"[bold]{escape(str(entity.get('enterprise_id') or ''))}[/]")
    grid.add_row("kind", escape(str(entity.get("entity_kind") or "")))
    if entity.get("modality"):
        grid.add_row("modality", escape(str(entity["modality"])))
    if entity.get("program_id"):
        grid.add_row("program", f"[cyan]{escape(str(entity['program_id']))}[/]")
    if entity.get("definition"):
        grid.add_row("definition", escape(str(entity["definition"])))
    xrefs = entity.get("exact_match_xrefs") or []
    if xrefs:
        grid.add_row("exactMatch", "  ".join(f"[magenta]{escape(x)}[/]" for x in xrefs))

    return Panel(grid, title="Enterprise Entity", border_style="green", box=box.ROUNDED)


def _graph_tree(ctx: dict[str, Any], *, canonical: str, entity: dict[str, Any]) -> Tree:
    label = entity.get("preferred_label_en") or canonical
    root = Tree(
        f"[bold bright_cyan]{escape(canonical)}[/]  [dim]{escape(str(label))}[/]",
        guide_style="dim",
    )

    targets_node = root.add("[bold]targets[/]")
    for t in ctx.get("targets") or []:
        targets_node.add(_entity_branch_line(t, label_style="green"))
    if not (ctx.get("targets") or []):
        targets_node.add("[dim]—[/]")

    diseases_node = root.add("[bold]investigates / diseases[/]")
    for d in ctx.get("diseases") or []:
        diseases_node.add(_entity_branch_line(d, label_style="yellow"))
    if not (ctx.get("diseases") or []):
        diseases_node.add("[dim]—[/]")

    # 其他关系（testedIn / hasAssay / belongsTo …）
    other = [
        c
        for c in (ctx.get("relationships") or [])
        if c.get("predicate") not in {"targets", "investigates"}
        and c.get("subject_id") == canonical
    ]
    if other:
        rel_node = root.add("[bold]relations[/]")
        for c in other:
            pred = escape(str(c.get("predicate") or ""))
            obj = escape(str(c.get("object_id") or c.get("object_value") or ""))
            conf = c.get("confidence")
            conf_s = f"  [dim]{float(conf):.2f}[/]" if conf is not None else ""
            rel_node.add(f"[cyan]{pred}[/] → {obj}{conf_s}")

    ev_node = root.add(f"[bold]evidence[/]  [dim]{len(ctx.get('evidence') or [])}[/]")
    for e in (ctx.get("evidence") or [])[:8]:
        style = _TYPE_STYLE.get(str(e.get("type") or ""), "white")
        etype = escape(str(e.get("type") or "?"))
        eid = escape(str(e.get("id") or ""))
        pred = escape(str(e.get("predicate") or ""))
        ev_node.add(f"[{style}]{etype}[/] [dim]{eid}[/]  {pred}")
    if len(ctx.get("evidence") or []) > 8:
        ev_node.add(f"[dim]+{len(ctx['evidence']) - 8} more[/]")

    n_assets = len(ctx.get("internal_assets") or [])
    asset_node = root.add(f"[bold]internal_assets[/]  [dim]{n_assets}[/]")
    for a in ctx.get("internal_assets") or []:
        at = escape(str(a.get("type") or "asset"))
        name = escape(str(a.get("name") or a.get("id") or ""))
        aid = escape(str(a.get("id") or ""))
        asset_node.add(f"[green]{at}[/]  {name}  [dim]{aid}[/]")

    return root


def _evidence_table(evidence: list[dict[str, Any]]) -> RenderableType:
    if not evidence:
        return Panel("[dim]no evidence[/]", border_style="dim", box=box.ROUNDED)

    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=False,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("type", width=8)
    table.add_column("claim / span", ratio=3, overflow="fold")
    table.add_column("conf", width=5, justify="right")
    table.add_column("id", style="dim", width=22, overflow="ellipsis")

    for i, e in enumerate(evidence, 1):
        style = _TYPE_STYLE.get(str(e.get("type") or ""), "white")
        etype = Text(str(e.get("type") or "?"), style=style)
        claim = e.get("claim")
        span = e.get("span")
        claim_s = _short_claim(str(claim)) if claim else "[dim]—[/]"
        block = Text.from_markup(claim_s)
        if span:
            block.append("\n")
            block.append("「", style="dim")
            block.append(escape(str(span)), style="italic")
            block.append("」", style="dim")
        conf = e.get("confidence")
        conf_s = f"{float(conf):.2f}" if conf is not None else "—"
        table.add_row(
            str(i),
            etype,
            block,
            conf_s,
            escape(str(e.get("id") or e.get("doc_id") or "")),
        )

    return Panel(table, title="Evidence", border_style="cyan", box=box.ROUNDED)


def _assets_table(assets: list[dict[str, Any]]) -> RenderableType:
    if not assets:
        return Panel("[dim]no internal assets[/]", border_style="dim", box=box.ROUNDED)

    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim")
    table.add_column("type", width=14)
    table.add_column("name", ratio=2)
    table.add_column("fqn", style="dim", ratio=2)
    table.add_column("url", style="dim", ratio=1)

    for a in assets:
        table.add_row(
            escape(str(a.get("type") or "")),
            escape(str(a.get("name") or "")),
            escape(str(a.get("id") or "")),
            escape(str(a.get("url") or "")),
        )

    desc_lines = [
        f"[dim]{escape(str(a.get('id')))}: {escape(str(a.get('description')))}[/]"
        for a in assets
        if a.get("description")
    ]
    body: RenderableType = table
    if desc_lines:
        body = Group(table, Text.from_markup("\n".join(desc_lines)))
    return Panel(body, title="OpenMetadata · Data Context", border_style="green", box=box.ROUNDED)


def _footer(ctx: dict[str, Any], *, canonical: str) -> Panel:
    n_t = len(ctx.get("targets") or [])
    n_d = len(ctx.get("diseases") or [])
    n_e = len(ctx.get("evidence") or [])
    n_a = len(ctx.get("internal_assets") or [])
    release = ctx.get("ontology_release_id") or "—"
    text = Text()
    text.append("✓ ", style="bold green")
    text.append("World Model query ready", style="bold")
    text.append(
        f"  ·  {escape(canonical)}  ·  "
        f"targets={n_t} diseases={n_d} evidence={n_e} assets={n_a}  ·  "
        f"release={escape(str(release))}",
        style="dim",
    )
    text.append("\n")
    text.append("next  ", style="dim")
    text.append("hmd foundation serve --mcp", style="cyan")
    text.append("  →  get_entity_context(", style="dim")
    text.append(escape(canonical), style="yellow")
    text.append(")", style="dim")
    return Panel(text, border_style="bright_green", box=box.ROUNDED)


def _primary_resolve_hit(result: dict[str, Any]) -> dict[str, Any] | None:
    resolve = result.get("resolve") or {}
    for hit in resolve.get("resolved") or []:
        if hit.get("canonical_entity"):
            return hit
    return None


def _entity_branch_line(row: dict[str, Any], *, label_style: str) -> str:
    label = escape(str(row.get("label") or row.get("id") or ""))
    eid = escape(str(row.get("id") or ""))
    ext = ", ".join(escape(x) for x in (row.get("external_ids") or [])[:4])
    line = f"[{label_style}]{label}[/]  [dim]{eid}[/]"
    if ext:
        line += f"  [magenta]{ext}[/]"
    return line


def _short_claim(claim: str) -> str:
    """HMD:ENT:DC:savolitinib targets HMD:ENT:TGT:MET → savolitinib targets MET"""
    parts = claim.split()
    if len(parts) < 3:
        return escape(claim)

    def _local(curie: str) -> str:
        if curie.startswith("HMD:ENT:"):
            return curie.rsplit(":", 1)[-1]
        return curie

    subj = _local(parts[0])
    pred = parts[1]
    obj = _local(parts[2]) if len(parts) > 2 else ""
    return f"[white]{escape(subj)}[/] [cyan]{escape(pred)}[/] [white]{escape(obj)}[/]"
