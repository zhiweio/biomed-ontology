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

__all__ = [
    "enrich_resolve",
    "render_evolve_mine",
    "render_golden_eval",
    "render_golden_eval_compact",
    "render_golden_path",
    "render_golden_path_compact",
    "render_resolve",
]

_CHECK_LABELS = {
    "ok": "golden_path ok",
    "no_yaml": "no YAML backend",
    "backends_graphdb": "GraphDB entity/rels",
    "backends_milvus": "Milvus evidence",
    "backends_om": "OpenMetadata assets",
    "bios_graphdb": "BIOS bridges",
    "bios_backend": "BIOS via GraphDB",
    "evidence_nonempty": "evidence nonempty",
    "assets_nonempty": "assets nonempty",
    "kb_search_nonempty": "KB search hits",
    "kb_restore_ok": "KB restore ok",
}

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
    ok = bool(result.get("ok"))
    ctx = result.get("context") or {}
    entity = ctx.get("entity") or {}
    canonical = result.get("canonical_entity") or ctx.get("enterprise_id") or "?"
    query = _resolve_query(result)

    # 早期失败（未 resolve / 无 context）：紧凑失败面板，不 dump 整份 dict
    if not ok and not ctx:
        out.print()
        out.print(
            _failure_header_panel(
                query=query,
                canonical=canonical if canonical != "?" else None,
                reason=str(result.get("reason") or "golden_path_failed"),
                path=result.get("path"),
            )
        )
        out.print()
        if result.get("resolve"):
            out.print(Rule("[dim]① Resolve[/]", style="dim"))
            out.print(_resolve_panel(result))
            out.print()
        out.print(_failure_footer(result, canonical=str(canonical)))
        out.print()
        return

    out.print()
    out.print(
        _header_panel(
            query=query,
            canonical=str(canonical),
            entity=entity,
            path=result.get("path"),
            ok=ok,
            reason=str(result.get("reason") or "") if not ok else None,
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
        out.print(_graph_tree(ctx, canonical=str(canonical), entity=entity))
        out.print()

        out.print(Rule("[dim]④ Evidence · Citationware[/]", style="dim"))
        out.print(_evidence_table(ctx.get("evidence") or []))
        out.print()

        out.print(Rule("[dim]⑤ Enterprise Assets[/]", style="dim"))
        out.print(_assets_table(ctx.get("internal_assets") or []))
        out.print()

        kb = result.get("kb")
        if kb is not None:
            out.print(Rule("[dim]⑥ Literature · ToolApi[/]", style="dim"))
            out.print(_kb_panel(kb))
            out.print()

        if not ok:
            out.print(Rule("[dim]Diagnosis[/]", style="dim"))
            out.print(_failure_diagnosis_panel(result))
            out.print()

    if ok:
        out.print(_footer(ctx, canonical=str(canonical)))
    else:
        out.print(_failure_footer(result, canonical=str(canonical)))
    out.print()


def render_golden_path_compact(result: dict[str, Any], *, console: Console | None = None) -> None:
    """仅步骤条 + 计数（旧行为的增强版）。"""
    render_golden_path(result, console=console, verbose=False)


def enrich_resolve(
    result: dict[str, Any],
    *,
    world: Any,
    fetch_graph_entity: Any | None = None,
) -> dict[str, Any]:
    """resolve 结果补全：按 canonical_entity 反查实体卡片与全部别名。

    优先 World Model seed；若提供 ``fetch_graph_entity`` 且可达，再合并 GraphDB
    的 altLabel / prefLabel（不覆盖本地已有表面形）。
    """
    rows: list[dict[str, Any]] = []
    for hit in result.get("resolved") or []:
        row = dict(hit)
        eid = hit.get("canonical_entity")
        entity: dict[str, Any] | None = None
        sources: list[str] = []
        if eid and world is not None:
            local = world.entity(eid)
            if local is not None:
                entity = local.to_dict()
                sources.append("world_model")
        if eid and fetch_graph_entity is not None:
            try:
                remote = fetch_graph_entity(eid)
            except Exception:  # noqa: BLE001 — CLI 展示路径，后端失败不阻断 resolve
                remote = None
            if remote:
                entity = _merge_entity_surfaces(entity, remote)
                sources.append("graphdb")
        query = str(result.get("query") or hit.get("mention") or "")
        surfaces = _alias_surfaces(entity or {}, query=query)
        row["entity"] = entity
        row["alias_source"] = "+".join(sources) if sources else None
        row["aliases"] = surfaces
        matched = next((s["label"] for s in surfaces if s.get("matched")), None)
        row["matched_surface"] = matched
        if entity and not row.get("entity_kind"):
            row["entity_kind"] = entity.get("entity_kind")
        if entity and not row.get("external_ids") and entity.get("exact_match_xrefs"):
            row["external_ids"] = list(entity["exact_match_xrefs"])
        rows.append(row)
    out = dict(result)
    out["resolved"] = rows
    return out


def render_resolve(
    result: dict[str, Any],
    *,
    console: Console | None = None,
) -> None:
    """Rich 展示 resolve_entity：命中 → 实体卡片 → 反查别名全集。"""
    out = console or Console()
    query = str(result.get("query") or "")
    hits = list(result.get("resolved") or [])
    mapped = [h for h in hits if h.get("canonical_entity")]
    primary = mapped[0] if mapped else (hits[0] if hits else None)

    out.print()
    out.print(_resolve_header_panel(query=query, primary=primary, hit_count=len(hits)))
    out.print()

    if not hits:
        out.print(
            Panel(
                "[dim]no resolve hits[/]",
                title="[bold red]UNMAPPED[/]",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        out.print()
        return

    for i, hit in enumerate(hits, 1):
        out.print(_resolve_hit_panel(hit, index=i, total=len(hits)))
        out.print()
        if hit.get("canonical_entity"):
            out.print(_resolve_aliases_panel(hit))
            out.print()

    out.print(_resolve_footer(result, primary=primary))
    out.print()


def render_golden_eval(
    summary: dict[str, Any],
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """多候选 Golden Path 评估：汇总表 +（可选）逐路径 checks。"""
    out = console or Console()
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = list(summary.get("failed") or [])
    paths = list(summary.get("paths") or [])
    ok = passed == total and total > 0

    out.print()
    out.print(_eval_header_panel(total=total, passed=passed, failed=failed, ok=ok))
    out.print()
    out.print(_eval_summary_table(paths))
    out.print()

    if verbose:
        for row in paths:
            out.print(_eval_path_panel(row))
            out.print()

    out.print(_eval_footer(total=total, passed=passed, ok=ok))
    out.print()


def render_golden_eval_compact(
    summary: dict[str, Any], *, console: Console | None = None
) -> None:
    """仅汇总表 + 页脚。"""
    render_golden_eval(summary, console=console, verbose=False)


def render_evolve_mine(
    result: Any,
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """Rich 展示 evolve-mine：候选 / 跳过 / 落库路径（不自动改本体）。"""
    out = console or Console()
    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    queries = list(payload.get("queries") or [])
    candidates = list(payload.get("candidates") or [])
    skipped = list(payload.get("skipped") or [])
    policy = payload.get("policy") or {}

    out.print()
    out.print(
        _evolve_header_panel(
            queries=queries,
            n_candidates=len(candidates),
            n_skipped=len(skipped),
            generated_at=str(payload.get("generated_at") or ""),
            auto_apply=bool(policy.get("auto_apply")),
        )
    )
    out.print()

    if verbose:
        out.print(_evolve_candidates_panel(candidates))
        out.print()
        if skipped:
            out.print(_evolve_skipped_panel(skipped, policy=policy))
            out.print()
        out.print(
            _evolve_artifacts_panel(
                kgcl_path=str(payload.get("kgcl_path") or ""),
                json_path=str(payload.get("json_path") or ""),
            )
        )
        out.print()

    out.print(
        _evolve_footer(
            n_candidates=len(candidates),
            n_skipped=len(skipped),
            kgcl_path=str(payload.get("kgcl_path") or ""),
        )
    )
    out.print()


def _resolve_query(result: dict[str, Any]) -> str:
    resolve = result.get("resolve") or {}
    return str(resolve.get("query") or result.get("query") or "")


def _header_panel(
    *,
    query: str,
    canonical: str,
    entity: dict[str, Any],
    path: str | None,
    ok: bool = True,
    reason: str | None = None,
) -> Panel:
    label_en = entity.get("preferred_label_en") or ""
    label_zh = entity.get("preferred_label_zh") or ""
    kind = entity.get("entity_kind") or "Entity"
    aliases = entity.get("aliases") or []

    title = Text()
    if ok:
        title.append("Golden Path", style="bold bright_white")
    else:
        title.append("Golden Path FAILED", style="bold red")
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
    if not ok and reason:
        body.append("\n")
        body.append("reason  ", style="dim")
        body.append(escape(reason), style="bold red")

    border = "bright_blue" if ok else "red"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _steps_panel(result: dict[str, Any]) -> Panel:
    ctx = result.get("context") or {}
    hit = _primary_resolve_hit(result)
    method = (hit or {}).get("resolution_method") or "—"
    conf = (hit or {}).get("confidence")
    kb = result.get("kb")
    resolve_ok = hit is not None and bool(hit.get("canonical_entity"))
    entity_ok = bool(ctx.get("entity"))
    targets_n = len(ctx.get("targets") or [])
    diseases_n = len(ctx.get("diseases") or [])
    evidence_n = len(ctx.get("evidence") or [])
    assets_n = len(ctx.get("internal_assets") or [])

    conf_s = f"  conf={conf:.2f}" if conf is not None else ""
    # WM 计数步：有 entity context 即视为通过（0 命中不标红，留给 diagnosis）
    rows: list[tuple[bool, str, str, str]] = [
        (resolve_ok, "1", "Resolve", f"[cyan]{escape(str(method))}[/]{conf_s}"),
        (entity_ok, "2", "Entity", "[green]ok[/]" if entity_ok else "[red]missing[/]"),
        (entity_ok, "3", "Targets", f"[bold]{targets_n}[/]"),
        (entity_ok, "4", "Diseases", f"[bold]{diseases_n}[/]"),
        (entity_ok, "5", "Evidence", f"[bold]{evidence_n}[/]  citationware"),
        (entity_ok, "6", "Assets", f"[bold]{assets_n}[/]  ELN/LIMS"),
    ]
    if kb is not None:
        kb_ok = bool(kb.get("ok"))
        hits = int(kb.get("hit_count") or 0)
        q = escape(str(kb.get("query") or ""))
        detail = f"[bold]{hits}[/] hits  query=[cyan]{q}[/]"
        if kb.get("query_original") and kb.get("query") != kb.get("query_original"):
            detail += f"  via alias from [dim]{escape(str(kb['query_original']))}[/]"
        rows.append((kb_ok, "7", "KB Lit", detail))

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("ok", width=2)
    table.add_column("step", style="dim", width=3)
    table.add_column("name", style="bold", width=10)
    table.add_column("detail")
    for step_ok, step, name, detail in rows:
        mark = "[green]✓[/]" if step_ok else "[red]✗[/]"
        table.add_row(mark, step, name, detail)

    border = "dim" if result.get("ok") else "red"
    return Panel(table, title="[bold]Trace[/]", border_style=border, box=box.SIMPLE)


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


def _kb_panel(kb: dict[str, Any]) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()
    ok = bool(kb.get("ok"))
    grid.add_row("status", "[bold green]OK[/]" if ok else "[bold red]FAIL[/]")
    grid.add_row("query", escape(str(kb.get("query") or "")))
    original = kb.get("query_original")
    if original and original != kb.get("query"):
        grid.add_row("original", escape(str(original)))
    tried = kb.get("query_tried") or []
    if len(tried) > 1:
        grid.add_row("tried", ", ".join(escape(str(q)) for q in tried[:6]))
    grid.add_row("hits", str(kb.get("hit_count") or 0))
    if kb.get("chunk_id"):
        grid.add_row("chunk", escape(str(kb["chunk_id"])))
    restore = kb.get("restore_ok")
    if restore is not None:
        grid.add_row("restore", "[green]ok[/]" if restore else "[yellow]skip/fail[/]")
    if kb.get("error"):
        grid.add_row("error", f"[red]{escape(str(kb['error']))}[/]")
    border = "green" if ok else "red"
    return Panel(grid, title="KB Literature Leg", border_style=border, box=box.ROUNDED)


def _footer(ctx: dict[str, Any], *, canonical: str) -> Panel:
    n_t = len(ctx.get("targets") or [])
    n_d = len(ctx.get("diseases") or [])
    n_e = len(ctx.get("evidence") or [])
    n_a = len(ctx.get("internal_assets") or [])
    release = ctx.get("ontology_release_id") or "—"
    backends = ctx.get("backends") or {}
    text = Text()
    text.append("✓ ", style="bold green")
    text.append("World Model query ready", style="bold")
    text.append(
        f"  ·  {escape(canonical)}  ·  "
        f"targets={n_t} diseases={n_d} evidence={n_e} assets={n_a}  ·  "
        f"release={escape(str(release))}",
        style="dim",
    )
    if backends:
        text.append("\n")
        text.append("backends  ", style="dim")
        parts = [
            f"entity={backends.get('entity')}",
            f"rels={backends.get('relationships')}",
            f"evidence={backends.get('evidence')}",
            f"assets={backends.get('assets')}",
        ]
        text.append("  ".join(parts), style="cyan")
    text.append("\n")
    text.append("next  ", style="dim")
    text.append("hmd serve --mcp", style="cyan")
    text.append("  →  get_entity_context(", style="dim")
    text.append(escape(canonical), style="yellow")
    text.append(")", style="dim")
    return Panel(text, border_style="bright_green", box=box.ROUNDED)


_FAIL_REASON_HINTS = {
    "candidate_unresolved": "mention not in enterprise dictionary / seed aliases",
    "entity_context_missing": "GraphDB entity card missing after resolve",
    "kb_search_empty": "literature search returned 0 hits (try EN preferred label)",
    "kb_restore_failed": "search hit but restore_context failed (citationware)",
    "kb_leg_failed": "literature search + restore leg did not pass",
}


def _failure_header_panel(
    *,
    query: str,
    canonical: str | None,
    reason: str,
    path: str | None,
) -> Panel:
    title = Text()
    title.append("Golden Path FAILED", style="bold red")
    if path:
        title.append("  ·  ", style="dim")
        title.append(str(path), style="dim cyan")

    body = Text()
    if query:
        body.append(escape(query), style="bold yellow")
        if canonical:
            body.append("  →  ", style="dim")
            body.append(escape(canonical), style="bold bright_cyan")
        else:
            body.append("  →  ", style="dim")
            body.append("UNMAPPED", style="bold red")
    elif canonical:
        body.append(escape(canonical), style="bold bright_cyan")
    else:
        body.append("no query", style="dim")
    body.append("\n")
    body.append("reason  ", style="dim")
    body.append(escape(reason), style="bold red")
    hint = _FAIL_REASON_HINTS.get(reason)
    if hint:
        body.append("\n")
        body.append("hint    ", style="dim")
        body.append(hint, style="dim")
    return Panel(body, title=title, border_style="red", box=box.ROUNDED, padding=(1, 2))


def _failure_diagnosis_panel(result: dict[str, Any]) -> Panel:
    reason = str(result.get("reason") or "golden_path_failed")
    evaluation = result.get("evaluation") or {}
    kb = result.get("kb") or {}
    backends = result.get("backends") or (result.get("context") or {}).get("backends") or {}

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=12)
    grid.add_column()
    grid.add_row("reason", f"[bold red]{escape(reason)}[/]")
    hint = _FAIL_REASON_HINTS.get(reason)
    if hint:
        grid.add_row("hint", escape(hint))

    if evaluation:
        checks = Table(box=None, show_header=False, padding=(0, 1))
        checks.add_column("ok", width=2)
        checks.add_column("name")
        for key, label in (
            ("backends_ok", "WM backends ok"),
            ("bios_graphdb", "BIOS bridges"),
            ("milvus_evidence", "Milvus evidence"),
            ("openmetadata_assets", "OM assets"),
            ("kb_search_nonempty", "KB search hits"),
            ("kb_restore_ok", "KB restore"),
        ):
            if key not in evaluation:
                continue
            value = evaluation.get(key)
            mark = "[green]✓[/]" if value else "[red]✗[/]"
            style = "white" if value else "red"
            checks.add_row(mark, f"[{style}]{escape(label)}[/]")
        grid.add_row("checks", checks)

    if kb:
        kb_bits = [
            f"hits={kb.get('hit_count') or 0}",
            f"restore={'ok' if kb.get('restore_ok') else 'fail'}",
            f"query={kb.get('query') or '—'}",
        ]
        tried = kb.get("query_tried") or []
        if tried:
            kb_bits.append("tried=" + ",".join(str(q) for q in tried[:4]))
        grid.add_row("kb", escape("  ".join(kb_bits)))
    if backends:
        parts = [f"{k}={v}" for k, v in backends.items()]
        grid.add_row("backends", escape("  ".join(parts)))

    return Panel(grid, title="Failure Diagnosis", border_style="red", box=box.ROUNDED)


def _failure_footer(result: dict[str, Any], *, canonical: str) -> Panel:
    reason = str(result.get("reason") or "golden_path_failed")
    query = _resolve_query(result)
    text = Text()
    text.append("✗ ", style="bold red")
    text.append("Golden Path failed", style="bold")
    text.append(f"  ·  {escape(reason)}", style="red")
    if canonical and canonical != "?":
        text.append(f"  ·  {escape(canonical)}", style="dim")
    text.append("\n")
    text.append("next  ", style="dim")
    if reason == "candidate_unresolved":
        text.append("hmd foundation resolve --text ", style="cyan")
        text.append(escape(query or "<mention>"), style="yellow")
        text.append("  ·  check ontology/catalog aliases", style="dim")
    elif reason.startswith("kb_"):
        text.append("hmd tools search --query ", style="cyan")
        entity = (result.get("context") or {}).get("entity") or {}
        en = entity.get("preferred_label_en") or "savolitinib"
        text.append(escape(str(en)), style="yellow")
        text.append("  ·  ", style="dim")
        text.append("hmd foundation golden --candidate ", style="cyan")
        text.append(escape(str(en)), style="yellow")
    else:
        text.append("hmd foundation golden --candidate ", style="cyan")
        text.append(escape(query or canonical or "HMPL-504"), style="yellow")
        text.append("  ·  ", style="dim")
        text.append("hmd serve --mcp", style="cyan")
    return Panel(text, border_style="red", box=box.ROUNDED)


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


def _eval_header_panel(
    *, total: int, passed: int, failed: list[str], ok: bool
) -> Panel:
    title = Text()
    title.append("Golden Eval", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("multi-path dual-surface", style="dim cyan")

    body = Text()
    style = "bold green" if ok else "bold red"
    body.append(f"{passed}/{total} passed", style=style)
    if failed:
        body.append("\n")
        body.append("failed  ", style="dim")
        body.append(", ".join(escape(c) for c in failed), style="red")
    else:
        body.append("\n")
        body.append("all candidates green · GraphDB / Milvus / OM · no YAML", style="dim")

    border = "bright_green" if ok else "red"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _eval_summary_table(paths: list[dict[str, Any]]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=False,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("ok", width=2)
    table.add_column("candidate", style="bold", min_width=12)
    table.add_column("kind", width=14)
    table.add_column("canonical", style="cyan", ratio=2, overflow="ellipsis")
    table.add_column("tgt", justify="right", width=4)
    table.add_column("dis", justify="right", width=4)
    table.add_column("ev", justify="right", width=4)
    table.add_column("ast", justify="right", width=4)
    table.add_column("bios", justify="right", width=5)

    for row in paths:
        counts = row.get("counts") or {}
        mark = "[green]✓[/]" if row.get("passed") else "[red]✗[/]"
        table.add_row(
            mark,
            escape(str(row.get("candidate") or "")),
            escape(str(row.get("entity_kind") or "—")),
            escape(str(row.get("canonical_entity") or "—")),
            str(counts.get("targets", 0)),
            str(counts.get("diseases", 0)),
            str(counts.get("evidence", 0)),
            str(counts.get("assets", 0)),
            str(counts.get("bios", 0)),
        )

    return Panel(table, title="[bold]Suite[/]", border_style="dim", box=box.SIMPLE)


def _eval_path_panel(row: dict[str, Any]) -> Panel:
    candidate = escape(str(row.get("candidate") or "?"))
    canonical = escape(str(row.get("canonical_entity") or "—"))
    passed = bool(row.get("passed"))
    checks = row.get("checks") or {}
    backends = row.get("backends") or {}
    bios = row.get("bios_bridges") or []

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()
    grid.add_row("canonical", f"[bold cyan]{canonical}[/]")
    if row.get("path"):
        grid.add_row("path", escape(str(row["path"])))
    if row.get("entity_kind"):
        grid.add_row("kind", escape(str(row["entity_kind"])))

    check_table = Table(box=None, show_header=False, padding=(0, 1))
    check_table.add_column("ok", width=2)
    check_table.add_column("name")
    for key, value in checks.items():
        mark = "[green]✓[/]" if value else "[red]✗[/]"
        label = _CHECK_LABELS.get(key, key)
        style = "white" if value else "red"
        check_table.add_row(mark, f"[{style}]{escape(label)}[/]")

    body_parts: list[RenderableType] = [grid, Text(), check_table]

    if backends:
        be = Text()
        be.append("backends  ", style="dim")
        parts = []
        for key, alias in (
            ("entity", "entity"),
            ("relationships", "rels"),
            ("evidence", "evidence"),
            ("assets", "assets"),
            ("bios", "bios"),
        ):
            val = backends.get(key)
            if val:
                parts.append(f"{alias}={val}")
        be.append("  ".join(parts), style="cyan")
        body_parts.extend([Text(), be])

    if bios:
        bios_line = Text()
        bios_line.append("bios  ", style="dim")
        shown = ", ".join(
            escape(str(b.get("bios_curie") or b.get("curie") or b)) for b in bios[:4]
        )
        more = f" +{len(bios) - 4}" if len(bios) > 4 else ""
        bios_line.append(shown + more, style="magenta")
        body_parts.extend([Text(), bios_line])

    status = "[bold green]PASS[/]" if passed else "[bold red]FAIL[/]"
    border = "green" if passed else "red"
    return Panel(
        Group(*body_parts),
        title=f"{status}  {candidate}",
        border_style=border,
        box=box.ROUNDED,
    )


def _eval_footer(*, total: int, passed: int, ok: bool) -> Panel:
    text = Text()
    if ok:
        text.append("✓ ", style="bold green")
        text.append("Golden Eval suite ready", style="bold")
    else:
        text.append("✗ ", style="bold red")
        text.append("Golden Eval suite failed", style="bold")
    text.append(
        f"  ·  {passed}/{total}  ·  backends=graphdb+milvus+om  ·  yaml_fallback=forbidden",
        style="dim",
    )
    text.append("\n")
    text.append("next  ", style="dim")
    text.append("hmd foundation golden --candidate HMPL-504", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd serve --mcp", style="cyan")
    border = "bright_green" if ok else "red"
    return Panel(text, border_style=border, box=box.ROUNDED)


# ---------------------------------------------------------------- resolve


def _merge_entity_surfaces(
    local: dict[str, Any] | None, remote: dict[str, Any]
) -> dict[str, Any]:
    base = dict(local or {})
    if not base:
        return dict(remote)
    aliases = list(base.get("aliases") or [])
    for a in remote.get("aliases") or []:
        if a and a not in aliases:
            aliases.append(a)
    for key in ("preferred_label_en", "preferred_label_zh"):
        if not base.get(key) and remote.get(key):
            base[key] = remote[key]
        elif remote.get(key) and remote[key] not in aliases and remote[key] != base.get(key):
            aliases.append(remote[key])
    xrefs = list(base.get("exact_match_xrefs") or [])
    for x in remote.get("exact_match_xrefs") or []:
        if x and x not in xrefs:
            xrefs.append(x)
    base["aliases"] = aliases
    base["exact_match_xrefs"] = xrefs
    if not base.get("entity_kind") and remote.get("entity_kind"):
        base["entity_kind"] = remote["entity_kind"]
    if not base.get("definition") and remote.get("definition"):
        base["definition"] = remote["definition"]
    return base


def _alias_surfaces(entity: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    """反查表面形：preferred_en/zh + aliases。

    仅查询命中的那一条带 ``matched: true``；其余不写 ``matched``，
    避免 JSON 里铺满 ``matched: false`` 造成“全未命中”的误读。
    """
    qnorm = _norm_surface(query)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: Any, role: str) -> None:
        text = str(label or "").strip()
        if not text:
            return
        key = text.casefold()
        is_query = bool(qnorm) and _norm_surface(text) == qnorm
        if key in seen:
            if is_query:
                for row in rows:
                    if row["label"].casefold() == key:
                        row["matched"] = True
            return
        seen.add(key)
        row: dict[str, Any] = {"label": text, "role": role}
        if is_query:
            row["matched"] = True
        rows.append(row)

    add(entity.get("preferred_label_en"), "preferred_en")
    add(entity.get("preferred_label_zh"), "preferred_zh")
    for alias in entity.get("aliases") or []:
        add(alias, "alias")
    # query 本身若未收录也补一行（罕见：词典命中但实体卡缺表面形）
    if qnorm and not any(r.get("matched") for r in rows):
        add(query, "query")
    return rows


def _norm_surface(text: str) -> str:
    return "".join(text.casefold().split())


def _resolve_header_panel(
    *, query: str, primary: dict[str, Any] | None, hit_count: int
) -> Panel:
    title = Text()
    title.append("Entity Resolve", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("dictionary / xref / surface", style="dim cyan")

    body = Text()
    body.append(escape(query) if query else "—", style="bold yellow")
    body.append("  →  ", style="dim")
    if primary and primary.get("canonical_entity"):
        body.append(escape(str(primary["canonical_entity"])), style="bold bright_cyan")
    else:
        body.append("UNMAPPED", style="bold red")
    body.append("\n")

    entity = (primary or {}).get("entity") or {}
    kind = primary.get("entity_kind") if primary else None
    kind = kind or entity.get("entity_kind") or "—"
    body.append(str(kind), style="green")
    label_en = entity.get("preferred_label_en")
    label_zh = entity.get("preferred_label_zh")
    if label_en:
        body.append("  ·  ", style="dim")
        body.append(escape(str(label_en)), style="white")
    if label_zh:
        body.append("  ·  ", style="dim")
        body.append(escape(str(label_zh)), style="white")
    body.append("\n")
    body.append(f"hits={hit_count}", style="dim")
    if primary and primary.get("alias_source"):
        body.append(f"  ·  aliases via {primary['alias_source']}", style="dim")

    ok = bool(primary and primary.get("canonical_entity"))
    return Panel(
        body,
        title=title,
        border_style="bright_green" if ok else "red",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def _resolve_hit_panel(hit: dict[str, Any], *, index: int, total: int) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=12)
    grid.add_column()

    mention = hit.get("mention") or ""
    canon = hit.get("canonical_entity")
    grid.add_row("mention", escape(str(mention)))
    if canon:
        grid.add_row("canonical", f"[bold cyan]{escape(str(canon))}[/]")
    else:
        grid.add_row("canonical", "[bold red]UNMAPPED[/]")
    grid.add_row("method", escape(str(hit.get("resolution_method") or "—")))
    conf = hit.get("confidence")
    if conf is not None:
        grid.add_row("confidence", f"{float(conf):.2f}")
    kind = hit.get("entity_kind") or (hit.get("entity") or {}).get("entity_kind")
    if kind:
        grid.add_row("kind", escape(str(kind)))
    ext = hit.get("external_ids") or (hit.get("entity") or {}).get("exact_match_xrefs") or []
    if ext:
        grid.add_row("external", "  ".join(f"[magenta]{escape(str(x))}[/]" for x in ext[:8]))
    bios = hit.get("bios_concepts") or []
    if bios:
        grid.add_row("bios", "  ".join(f"[magenta]{escape(str(x))}[/]" for x in bios[:6]))
    entity = hit.get("entity") or {}
    if entity.get("definition"):
        grid.add_row("definition", escape(str(entity["definition"])))

    border = "cyan" if canon else "red"
    title = f"Hit {index}/{total}"
    return Panel(grid, title=f"[bold]{title}[/]", border_style=border, box=box.ROUNDED)


def _resolve_aliases_panel(hit: dict[str, Any]) -> Panel:
    aliases = list(hit.get("aliases") or [])
    eid = escape(str(hit.get("canonical_entity") or ""))
    if not aliases:
        return Panel(
            f"[dim]no aliases for {eid}[/]",
            title="[bold]Aliases · reverse[/]",
            border_style="dim",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("", width=2)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("surface", style="bold", ratio=2, overflow="fold")
    table.add_column("role", style="dim", width=14)

    for i, row in enumerate(aliases, 1):
        matched = bool(row.get("matched"))
        mark = "[yellow]★[/]" if matched else " "
        label = escape(str(row.get("label") or ""))
        if matched:
            label = f"[bold yellow]{label}[/]"
        table.add_row(mark, str(i), label, escape(str(row.get("role") or "alias")))

    src = hit.get("alias_source") or "world_model"
    return Panel(
        table,
        title=f"[bold]Aliases · reverse[/]  [dim]{len(aliases)} · {escape(str(src))}[/]",
        border_style="green",
        box=box.ROUNDED,
    )


def _resolve_footer(result: dict[str, Any], *, primary: dict[str, Any] | None) -> Panel:
    text = Text()
    if primary and primary.get("canonical_entity"):
        text.append("✓ ", style="bold green")
        text.append("Resolved", style="bold")
        text.append(
            f"  ·  {escape(str(primary['canonical_entity']))}  ·  "
            f"aliases={len(primary.get('aliases') or [])}  ·  "
            f"release={escape(str(result.get('ontology_release_id') or '—'))}",
            style="dim",
        )
        text.append("\n")
        text.append("next  ", style="dim")
        text.append(
            f"hmd foundation golden --candidate {escape(str(result.get('query') or ''))}",
            style="cyan",
        )
        text.append("  ·  ", style="dim")
        text.append("hmd serve --mcp", style="cyan")
        border = "bright_green"
    else:
        text.append("✗ ", style="bold red")
        text.append("Unresolved mention", style="bold")
        text.append("  ·  check enterprise_dictionary / entities seed", style="dim")
        border = "red"
    return Panel(text, border_style=border, box=box.ROUNDED)


# ---------------------------------------------------------------- evolve-mine


def _evolve_header_panel(
    *,
    queries: list[str],
    n_candidates: int,
    n_skipped: int,
    generated_at: str,
    auto_apply: bool,
) -> Panel:
    title = Text()
    title.append("Evolve Mine", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("Data Loop · candidates only", style="dim cyan")

    body = Text()
    body.append(f"queries={len(queries)}", style="bold")
    body.append("  →  ", style="dim")
    body.append(f"candidates={n_candidates}", style="bold yellow")
    body.append("  ·  ", style="dim")
    body.append(f"skipped={n_skipped}", style="dim")
    body.append("\n")
    if queries:
        shown = ", ".join(escape(q) for q in queries[:6])
        more = f" +{len(queries) - 6}" if len(queries) > 6 else ""
        body.append(shown + more, style="white")
        body.append("\n")
    body.append(f"stamp={escape(generated_at) if generated_at else '—'}", style="dim")
    body.append("  ·  ", style="dim")
    if auto_apply:
        body.append("auto_apply=ON", style="bold red")
    else:
        body.append("auto_apply=forbidden", style="green")

    border = "yellow" if n_candidates else "bright_green"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _evolve_candidates_panel(candidates: list[dict[str, Any]]) -> Panel:
    if not candidates:
        return Panel(
            "[dim]no unmapped / low-confidence signals — nothing to curate[/]",
            title="[bold]Candidates[/]",
            border_style="dim",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("mention", style="bold yellow", ratio=2, overflow="fold")
    table.add_column("method", width=12)
    table.add_column("conf", width=5, justify="right")
    table.add_column("op", width=16)
    table.add_column("canonical", style="cyan", ratio=2, overflow="fold")

    for i, c in enumerate(candidates, 1):
        conf = c.get("confidence")
        conf_s = f"{float(conf):.2f}" if conf is not None else "—"
        op = str(c.get("suggested_op") or "")
        op_style = "yellow" if op == "create synonym" else "magenta"
        canon = c.get("canonical_entity") or "—"
        table.add_row(
            str(i),
            escape(str(c.get("mention") or "")),
            escape(str(c.get("resolution_method") or "—")),
            conf_s,
            f"[{op_style}]{escape(op)}[/]",
            escape(str(canon)),
        )

    return Panel(
        table,
        title=f"[bold]Candidates[/]  [dim]{len(candidates)} · curate before apply[/]",
        border_style="yellow",
        box=box.ROUNDED,
    )


def _evolve_skipped_panel(
    skipped: list[dict[str, Any]], *, policy: dict[str, Any]
) -> Panel:
    thr = policy.get("min_confidence_skip")
    thr_s = f"{float(thr):.2f}" if thr is not None else "0.95"

    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("mention", style="bold", ratio=2, overflow="fold")
    table.add_column("canonical", style="cyan", ratio=2, overflow="fold")
    table.add_column("conf", width=5, justify="right")
    table.add_column("reason", style="dim", ratio=2, overflow="fold")

    for i, s in enumerate(skipped, 1):
        conf = s.get("confidence")
        conf_s = f"{float(conf):.2f}" if conf is not None else "—"
        table.add_row(
            str(i),
            escape(str(s.get("mention") or "")),
            escape(str(s.get("canonical_entity") or "—")),
            conf_s,
            escape(str(s.get("reason") or "")),
        )

    return Panel(
        table,
        title=f"[bold]Skipped[/]  [dim]{len(skipped)} · mapped conf≥{thr_s}[/]",
        border_style="green",
        box=box.ROUNDED,
    )


def _evolve_artifacts_panel(*, kgcl_path: str, json_path: str) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=8)
    grid.add_column()
    grid.add_row("kgcl", f"[cyan]{escape(kgcl_path)}[/]")
    grid.add_row("json", f"[cyan]{escape(json_path)}[/]")
    return Panel(grid, title="[bold]Artifacts[/]", border_style="dim", box=box.ROUNDED)


def _evolve_footer(*, n_candidates: int, n_skipped: int, kgcl_path: str) -> Panel:
    text = Text()
    if n_candidates:
        text.append("✓ ", style="bold yellow")
        text.append("Candidates staged", style="bold")
        text.append(
            f"  ·  signals={n_candidates}  ·  skipped={n_skipped}  ·  auto_apply=forbidden",
            style="dim",
        )
        text.append("\n")
        text.append("next  ", style="dim")
        text.append("review KGCL TODO lines", style="cyan")
        text.append("  ·  ", style="dim")
        text.append("hmd foundation resolve <mention>", style="cyan")
        if kgcl_path:
            text.append("\n")
            text.append("file  ", style="dim")
            text.append(escape(kgcl_path), style="dim")
        border = "yellow"
    else:
        text.append("✓ ", style="bold green")
        text.append("No evolution signals", style="bold")
        text.append(
            f"  ·  skipped={n_skipped}  ·  all mapped high-confidence",
            style="dim",
        )
        text.append("\n")
        text.append("next  ", style="dim")
        text.append("hmd foundation resolve <mention>", style="cyan")
        text.append("  ·  ", style="dim")
        text.append("hmd serve --mcp", style="cyan")
        border = "bright_green"
    return Panel(text, border_style=border, box=box.ROUNDED)
