"""Demo 场景的 Rich 终端渲染（对齐 `hmd foundation golden` 风格）。

对 `DemoResult.lines` 做**通用**行分类，不按 demo_id 写死：
命中 / 事实边 / 证据树 / kv 指标 / 还原 / 叙述。
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from biomed_ontology.demo import DemoResult

__all__ = ["render_demo_results", "render_demo_results_compact"]

_MOD_STYLE = {
    "IMAGE": "magenta",
    "TEXT": "cyan",
    "TABLE": "yellow",
    "FORMULA": "green",
}

_BACKEND_STYLE = {
    "graphdb": "cyan",
    "graphdb_biomedical": "cyan",
    "graphdb_biomedical_empty": "dim cyan",
    "milvus": "magenta",
    "openmetadata": "green",
    "yaml": "red",
    "resolver": "yellow",
}

#   [IMAGE] DOC:…#sec :: snippet
#   [IMAGE] DOC:… p6 :: snippet
#   + DOC:…#sec :: snippet
_HIT_RE = re.compile(
    r"^\s*(?:"
    r"\+\s+"
    r"|\[(?P<mod>[A-Za-z]+)\s*\]\s+"
    r")?"
    r"(?P<body>(?:DOC|HMD|ENT|CHK):\S.*?)"
    r"\s*::\s*"
    r"(?P<snip>.+)$"
)
_HIT_PAGE_RE = re.compile(r"^(?P<ref>\S+)\s+p(?P<page>\d+)$")

#   fruquintinib -has_target-> kinase … [TEXT] ← DOC:PMID.x p1 Abstract
_FACT_RE = re.compile(
    r"^\s*(?P<subj>.+?)\s+"
    r"-(?P<pred>[A-Za-z_][\w]*)->"
    r"\s+(?P<obj>.+?)\s+"
    r"\[(?P<mod>[A-Za-z]+)\]\s*←\s*"
    r"(?P<source>.+)$"
)

#   DOC:CTGOV.x 碎片 1 个 → 章节 1 处：BriefSummary
_TREE_RE = re.compile(
    r"^\s*(?P<doc>DOC:\S+)\s+"
    r"碎片\s+(?P<chunks>\d+)\s+个\s*→\s*"
    r"章节\s+(?P<secs>\d+)\s+处：(?P<paths>.*)$"
)

#   还原 CHK:txt.xxx：breadcrumb p1-1，300 字碎片 → 312 字全节（…）
_RESTORE_RE = re.compile(r"^还原\s+(?P<chunk>\S+)：(?P<body>.+)$")

#   事实：无凭据 7 条… / 有凭据 8 条…
#   受限文档 DOC:x：无凭据还原 0 字… / 有凭据还原 354 字
_COMPARE_RE = re.compile(
    r"^(?P<label>.+?)\s*(?P<free>无凭据.+?)\s*/\s*(?P<paid>有凭据.+)$"
)

#   targets=1 evidence=6 backends={...}
_METRICS_LEAD_RE = re.compile(r"^[A-Za-z_][\w]*=")
_KV_RE = re.compile(
    r"([A-Za-z_][\w]*)="
    r"("
    r"\{[^{}]*\}"  # flat dict literal
    r"|\[(?:[^\[\]])*\]"  # list literal
    r"|[^\s]+"  # scalar token
    r")"
)

_DICT_RE = re.compile(r"\{[^{}]+\}")
_LIST_RE = re.compile(r"\[(?:[^\[\]])*\]")
_MOD_LIST_RE = re.compile(r"modalities=\[([^\]]+)\]")
_MOD_WORD_RE = re.compile(r"\b(IMAGE|TEXT|TABLE|FORMULA)\b")
_CURIE_RE = re.compile(r"\b((?:DOC|HMD|CHK|ENT|HMDF)[:=][^\s,;]+)")

#   接地概念 ['HMD:ENT:…', …]
_CONCEPTS_RE = re.compile(r"^\s*接地概念\s+(\[.*\])\s*$")

#   HMPL-504 → HMD:ENT:DC:savolitinib
_ARROW_RE = re.compile(r"^\s*(?P<left>.+?)\s*→\s*(?P<right>\S.+?)\s*$")

#   [P0] cooccurrence_anomaly 'payload' x6
_SIGNAL_RE = re.compile(
    r"^\s*\[(?P<pri>P\d+)\]\s+(?P<stype>[A-Za-z_][\w]*)\s+"
    r"(?P<payload>'[^']*'|\"[^\"]*\"|\S+)\s+x(?P<n>\d+)\s*$"
)

#   create edge … / create exact synonym …
_KGCL_RE = re.compile(
    r"^\s*(?P<op>create edge|create exact synonym|create|delete|obsolete)\b\s*(?P<body>.+)$",
    re.IGNORECASE,
)

#   KB concept_id=HMD:…   /   WM enterprise_id=HMD:…
_SIMPLE_KV_RE = re.compile(r"^\s*(?P<key>[A-Za-z][\w ./-]{0,40}?)=(?P<val>\S+)\s*$")

#   decision[ABSTAIN] chosen=None conf=0.55 …
_DECISION_RE = re.compile(r"^\s*decision\[(?P<stage>[^\]]+)\]\s+(?P<body>.+)$")
_CAND_RE = re.compile(
    r"^\s*候选\s+(?P<cid>\S+)\s+score=(?P<score>[\d.]+)\s+ch=(?P<ch>\S+)\s*$"
)
_SPAN_RE = re.compile(r"^\s*span\s+(?P<name>\S+)\s+(?P<ms>[\d.]+)ms\s*(?P<attrs>.*)$")
_QUOTE_RE = re.compile(r"^\s*[“\"](.+)[”\"]\s*$")


def render_demo_results(
    results: list[DemoResult],
    *,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """分场景展示语义层能力验收（术语 / 扩展 / 许可 / Citationware …）。"""
    out = console or Console()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    all_ok = passed == total and total > 0

    out.print()
    out.print(_header_panel(results, passed=passed, total=total, all_ok=all_ok))
    out.print()
    out.print(_trace_panel(results))
    out.print()

    if verbose:
        for r in results:
            out.print(
                Rule(
                    f"[dim]{escape(r.demo_id)} · {escape(r.title)}[/]",
                    style="dim",
                )
            )
            out.print(_demo_panel(r))
            out.print()

    out.print(_footer(passed=passed, total=total, all_ok=all_ok, results=results))
    out.print()


def render_demo_results_compact(
    results: list[DemoResult],
    *,
    console: Console | None = None,
) -> None:
    """仅 Trace 摘要。"""
    render_demo_results(results, console=console, verbose=False)


def _header_panel(
    results: list[DemoResult],
    *,
    passed: int,
    total: int,
    all_ok: bool,
) -> Panel:
    title = Text()
    title.append("Semantic Layer Demo", style="bold bright_white")
    title.append("  ·  ", style="dim")
    title.append("KB · World Model · Bridge", style="dim cyan")

    body = Text()
    body.append(f"{passed}/{total}", style="bold bright_cyan" if all_ok else "bold yellow")
    body.append("  scenarios passed\n", style="dim")

    buckets = _surface_buckets(results)
    parts = []
    for label, rows in buckets:
        if not rows:
            continue
        ok_n = sum(1 for r in rows if r.passed)
        style = "green" if ok_n == len(rows) else "yellow"
        parts.append(f"[{style}]{label} {ok_n}/{len(rows)}[/]")
    if parts:
        body.append_text(Text.from_markup("  ".join(parts)))
        body.append("\n")

    if all_ok:
        body.append("status  ", style="dim")
        body.append("OK", style="bold green")
        body.append("  ·  falsifiable claims, not print-only", style="dim")
    else:
        failed = [r.demo_id for r in results if not r.passed]
        body.append("status  ", style="dim")
        body.append("FAILED", style="bold red")
        body.append("  ·  ", style="dim")
        body.append(", ".join(failed), style="red")

    border = "bright_green" if all_ok else "red"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 2))


def _trace_panel(results: list[DemoResult]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("ok", width=2)
    table.add_column("id", style="bold", width=4)
    table.add_column("surface", style="dim", width=10)
    table.add_column("title", style="bold", min_width=10, ratio=1)
    table.add_column("claim", style="dim", ratio=2, overflow="ellipsis", no_wrap=True)

    for r in results:
        mark = "[green]✓[/]" if r.passed else "[red]✗[/]"
        claim = escape(_short_claim(r.claim, 72))
        if not r.passed:
            claim = f"[red]{claim}[/]"
        table.add_row(
            mark,
            escape(r.demo_id),
            _surface_of(r.demo_id),
            escape(r.title),
            claim,
        )

    return Panel(table, title="[bold]Trace[/]", border_style="dim", box=box.SIMPLE)


def _demo_panel(result: DemoResult) -> Panel:
    status = Text()
    if result.passed:
        status.append("PASS", style="bold green")
    else:
        status.append("FAIL", style="bold red")

    claim = Text(escape(result.claim), style="italic")

    head = Table.grid(padding=(0, 2))
    head.add_column(style="dim", justify="right", min_width=8)
    head.add_column()
    head.add_row("status", status)
    head.add_row("claim", claim)

    detail = _render_detail_lines(result.lines, passed=result.passed)
    body = Group(head, Text(""), detail) if detail else head
    border = "green" if result.passed else "red"
    title = f"[bold]{escape(result.demo_id)}[/]  {escape(result.title)}"
    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(1, 1))


def _footer(
    *,
    passed: int,
    total: int,
    all_ok: bool,
    results: list[DemoResult],
) -> Panel:
    text = Text()
    if all_ok:
        text.append("✓ ", style="bold green")
        text.append("Semantic layer ready", style="bold")
    else:
        text.append("✗ ", style="bold red")
        text.append("Semantic layer regressions", style="bold")
    text.append(f"  ·  passed={passed}/{total}", style="dim")

    for label, rows in _surface_buckets(results):
        if not rows:
            continue
        ok_n = sum(1 for r in rows if r.passed)
        text.append(f"  ·  {label}={ok_n}/{len(rows)}", style="dim")

    text.append("\n")
    text.append("next  ", style="dim")
    text.append("hmd serve --mcp", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd foundation golden --candidate HMPL-504", style="cyan")
    text.append("  ·  ", style="dim")
    text.append("hmd eval --entitlements MOCK_LICENSED", style="cyan")
    border = "bright_green" if all_ok else "red"
    return Panel(text, border_style=border, box=box.ROUNDED)


# ---------------------------------------------------------------- classify + render


def _flatten_line(line: str) -> str:
    """语料 snippet 常夹杂换行；分类前压成单行，避免 `$` / `.` 匹配失败。"""
    return re.sub(r"\s+", " ", line.replace("\r\n", "\n").replace("\n", " ")).rstrip()


def _classify_line(line: str) -> tuple[str, Any]:
    """通用行分类。返回 (kind, payload)。不按 demo_id 分支。"""
    flat = _flatten_line(line)
    if concepts := _parse_concepts(flat):
        return "concepts", concepts
    if hit := _parse_hit(flat):
        return "hit", hit
    if fact := _parse_fact(flat):
        return "fact", fact
    if tree := _parse_tree(flat):
        return "tree", tree
    if signal := _parse_signal(flat):
        return "signal", signal
    if kgcl := _parse_kgcl(flat):
        return "kgcl", kgcl
    if decision := _parse_decision(flat):
        return "decision", decision
    if cand := _parse_candidate(flat):
        return "candidate", cand
    if span := _parse_span(flat):
        return "span", span
    if metrics := _parse_metrics(flat):
        return "metrics", metrics
    if kv := _parse_simple_kv(flat):
        return "metrics", kv
    if arrow := _parse_arrow(flat):
        return "arrow", arrow
    if quote := _parse_quote(flat):
        return "quote", quote
    if restore := _parse_restore(flat):
        return "restore", restore
    if compare := _parse_compare(flat):
        return "compare", compare
    return "note", flat if flat != line.strip() else line


def _render_detail_lines(lines: list[str], *, passed: bool) -> RenderableType:
    if not lines:
        return Text("—", style="dim")

    parts: list[RenderableType] = []
    buf_kind: str | None = None
    buf: list[Any] = []

    def flush() -> None:
        nonlocal buf_kind, buf
        if not buf_kind or not buf:
            buf_kind, buf = None, []
            return
        if buf_kind == "hit":
            parts.append(_hits_table(buf))
        elif buf_kind == "fact":
            parts.append(_facts_table(buf))
        elif buf_kind == "tree":
            parts.append(_tree_table(buf))
        elif buf_kind == "metrics":
            merged: list[tuple[str, Any]] = []
            for m in buf:
                merged.extend(m.get("pairs") or [])
            parts.append(_metrics_panel({"pairs": merged}))
        elif buf_kind == "signal":
            parts.append(_signals_table(buf))
        elif buf_kind == "kgcl":
            parts.append(_kgcl_table(buf))
        elif buf_kind == "arrow":
            parts.append(_arrows_table(buf))
        elif buf_kind == "decision":
            for m in buf:
                parts.append(_decision_panel(m))
        elif buf_kind == "span":
            parts.append(_spans_table(buf))
        elif buf_kind == "candidate":
            parts.append(_candidates_table(buf))
        elif buf_kind == "quote":
            parts.append(_quotes_panel(buf))
        elif buf_kind == "restore":
            for m in buf:
                parts.append(_restore_panel(m))
        elif buf_kind == "compare":
            for m in buf:
                parts.append(_compare_panel(m, passed=passed))
        else:
            for note in buf:
                parts.append(_styled_narrative(str(note), passed=passed))
        buf_kind, buf = None, []

    for raw in lines:
        kind, payload = _classify_line(raw)
        # 接地概念挂到上一条 hit，不打断 Hits 合并
        if kind == "concepts" and buf_kind == "hit" and buf:
            buf[-1]["concepts"] = payload
            continue
        mergeable = kind in {
            "hit",
            "fact",
            "tree",
            "metrics",
            "signal",
            "kgcl",
            "arrow",
            "span",
            "candidate",
            "quote",
        }
        if buf_kind is None:
            buf_kind, buf = kind, [payload]
            continue
        if mergeable and kind == buf_kind:
            buf.append(payload)
            continue
        flush()
        buf_kind, buf = kind, [payload]
    flush()

    if len(parts) == 1:
        return parts[0]

    spaced: list[RenderableType] = []
    prev_block = False
    for i, p in enumerate(parts):
        is_block = isinstance(p, Panel)
        if i and (is_block or prev_block):
            spaced.append(Text(""))
        spaced.append(p)
        prev_block = is_block
    return Group(*spaced)


# --- parsers ---


def _parse_hit(line: str) -> dict[str, Any] | None:
    # 直接调用时也压平：snippet 里的 \\n 会让 `.+` / `$` 失败
    m = _HIT_RE.match(_flatten_line(line))
    if not m:
        return None
    body = (m.group("body") or "").strip()
    page_m = _HIT_PAGE_RE.match(body)
    if page_m:
        ref, page = page_m.group("ref"), page_m.group("page")
    else:
        ref, page = body, None
    mod = m.group("mod")
    snip = re.sub(r"\s+", " ", (m.group("snip") or "").strip())
    return {
        "mod": mod.upper() if mod else None,
        "ref": ref,
        "page": page,
        "snip": snip or None,
        "concepts": None,
    }


def _parse_concepts(line: str) -> list[str] | None:
    m = _CONCEPTS_RE.match(line)
    if not m:
        return None
    data = _safe_literal(m.group(1))
    if isinstance(data, list):
        return [str(x) for x in data]
    return None


def _parse_arrow(line: str) -> dict[str, str] | None:
    # 排除 compare / tree 等已含箭头语义的行
    if "无凭据" in line or "有凭据" in line or "碎片" in line:
        return None
    m = _ARROW_RE.match(line)
    if not m:
        return None
    left, right = m.group("left").strip(), m.group("right").strip()
    if not left or not right:
        return None
    # 叙述句里的 "→ 阻断" 等：右侧过长或含空格中文谓语时仍接受（W1 右侧是 CURIE）
    return {"left": left, "right": right}


def _parse_signal(line: str) -> dict[str, str] | None:
    m = _SIGNAL_RE.match(line)
    if not m:
        return None
    payload = m.group("payload").strip("'\"")
    return {
        "priority": m.group("pri"),
        "type": m.group("stype"),
        "payload": payload,
        "n": m.group("n"),
    }


def _parse_kgcl(line: str) -> dict[str, str] | None:
    m = _KGCL_RE.match(line)
    if not m:
        return None
    return {"op": m.group("op").lower(), "body": m.group("body").strip()}


def _parse_decision(line: str) -> dict[str, str] | None:
    m = _DECISION_RE.match(line)
    if not m:
        return None
    body = m.group("body")
    fields = dict(re.findall(r"(\w+)=(\S+)", body))
    return {
        "stage": m.group("stage"),
        "chosen": fields.get("chosen", "—"),
        "conf": fields.get("conf", "—"),
        "model": fields.get("model", "—"),
        "raw": body,
    }


def _parse_candidate(line: str) -> dict[str, str] | None:
    m = _CAND_RE.match(line)
    if not m:
        return None
    return {
        "id": m.group("cid"),
        "score": m.group("score"),
        "channel": m.group("ch"),
    }


def _parse_span(line: str) -> dict[str, Any] | None:
    m = _SPAN_RE.match(line)
    if not m:
        return None
    attrs_raw = (m.group("attrs") or "").strip()
    attrs = _safe_literal(attrs_raw) if attrs_raw.startswith("{") else attrs_raw
    return {
        "name": m.group("name"),
        "ms": m.group("ms"),
        "attrs": attrs,
    }


def _parse_quote(line: str) -> str | None:
    m = _QUOTE_RE.match(line)
    return m.group(1).strip() if m else None


def _parse_simple_kv(line: str) -> dict[str, Any] | None:
    """`KB concept_id=HMD:…` 这类带空格前缀的单键值。"""
    if _parse_metrics(line):
        return None
    m = _SIMPLE_KV_RE.match(line)
    if not m:
        return None
    key = re.sub(r"\s+", " ", m.group("key")).strip()
    val = _coerce_value(m.group("val"))
    # 避免把普通叙述里的 lone token 吃掉：值需像 CURIE / 数字 / 标识符
    if isinstance(val, str) and ":" not in val and not re.fullmatch(r"[\w.-]+", val):
        return None
    return {"pairs": [(key, val)]}


def _parse_fact(line: str) -> dict[str, str] | None:
    m = _FACT_RE.match(line)
    if not m:
        return None
    return {
        "subject": m.group("subj").strip(),
        "predicate": m.group("pred").strip(),
        "object": m.group("obj").strip(),
        "mod": m.group("mod").upper(),
        "source": m.group("source").strip(),
    }


def _parse_tree(line: str) -> dict[str, str] | None:
    m = _TREE_RE.match(line)
    if not m:
        return None
    return {
        "doc": m.group("doc"),
        "chunks": m.group("chunks"),
        "secs": m.group("secs"),
        "paths": (m.group("paths") or "").strip(),
    }


def _parse_metrics(line: str) -> dict[str, Any] | None:
    """整行以 `key=value` 为主（可夹 dict），如 W2 targets/evidence/backends。"""
    stripped = line.strip()
    if not _METRICS_LEAD_RE.match(stripped):
        return None
    # 排除明显叙述（含中文冒号陈述等），但仍允许 backends= 混排
    if "：" in stripped and "=" not in stripped.split("：", 1)[0]:
        return None

    pairs: list[tuple[str, Any]] = []
    pos = 0
    for m in _KV_RE.finditer(stripped):
        if m.start() > pos and stripped[pos : m.start()].strip():
            # key=value 之间夹了无法解释的文本 → 不是 metrics 行
            return None
        key = m.group(1)
        raw_val = m.group(2)
        pairs.append((key, _coerce_value(raw_val)))
        pos = m.end()
    if pos < len(stripped) and stripped[pos:].strip():
        return None
    if len(pairs) < 1:
        return None
    # 单 key：仅在值像指标/CURIE/容器时收编，其余交 simple_kv 或叙述
    if len(pairs) == 1:
        val = pairs[0][1]
        if isinstance(val, (int, float, dict, list)):
            return {"pairs": pairs}
        if isinstance(val, str) and (":" in val or re.fullmatch(r"[\w.-]+", val)):
            return {"pairs": pairs}
        return None
    return {"pairs": pairs}


def _parse_compare(line: str) -> dict[str, str] | None:
    m = _COMPARE_RE.match(line.strip())
    if not m:
        return None
    label = (m.group("label") or "").strip()
    # 标签太短或整行几乎无结构时交给叙述
    if len(label) < 2:
        return None
    return {
        "label": label.rstrip("：:"),
        "free": m.group("free").strip(),
        "paid": m.group("paid").strip(),
    }


def _parse_restore(line: str) -> dict[str, str] | None:
    m = _RESTORE_RE.match(line.strip())
    if not m:
        return None
    body = m.group("body")
    # breadcrumb p1-1，300 字碎片 → 312 字全节（共 1 个碎片，截断=False）
    meta = ""
    main = body
    if "（" in body and body.endswith("）"):
        main, meta = body.rsplit("（", 1)
        meta = meta[:-1]
    page = ""
    crumb = main
    pm = re.search(r"\s+p(\d+)-(\d+)，?", main)
    if pm:
        crumb = main[: pm.start()].strip()
        page = f"{pm.group(1)}-{pm.group(2)}"
        rest = main[pm.end() :].strip()
    else:
        rest = ""
    return {
        "chunk": m.group("chunk"),
        "breadcrumb": crumb,
        "page": page,
        "detail": rest,
        "meta": meta,
    }


def _coerce_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        try:
            return ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return raw
    try:
        if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
            return int(raw)
        return float(raw) if re.fullmatch(r"-?\d+\.\d+", raw) else raw
    except ValueError:
        return raw


# --- table / panel builders ---


def _hits_table(hits: list[dict[str, Any]]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=False,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    has_mod = any(h.get("mod") for h in hits)
    has_page = any(h.get("page") for h in hits)
    has_concepts = any(h.get("concepts") for h in hits)
    if has_mod:
        table.add_column("mod", width=6)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("ref", style="cyan", ratio=2, overflow="ellipsis", no_wrap=True)
    if has_page:
        table.add_column("page", width=5, justify="right")
    table.add_column("snippet", ratio=3, overflow="fold")
    if has_concepts:
        table.add_column("concepts", ratio=2, overflow="fold")

    for i, h in enumerate(hits, 1):
        row: list[str | Text] = []
        if has_mod:
            mod = h.get("mod") or "—"
            row.append(Text(str(mod), style=_MOD_STYLE.get(str(mod), "white")))
        row.append(str(i))
        row.append(escape(str(h.get("ref") or "")))
        if has_page:
            row.append(str(h.get("page") or "—"))
        snip = h.get("snip")
        row.append(escape(str(snip)) if snip else "—")
        if has_concepts:
            concepts = h.get("concepts") or []
            row.append(_format_mapping(concepts) if concepts else Text("—", style="dim"))
        table.add_row(*row)

    return Panel(
        table,
        title=f"[bold]Hits[/]  [dim]{len(hits)}[/]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _facts_table(facts: list[dict[str, str]]) -> Panel:
    """边列表（非宽表）：窄终端下也能读完整 predicate / object。"""
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(style="dim", width=3, justify="right")
    grid.add_column(ratio=1)

    for i, f in enumerate(facts, 1):
        mod = f.get("mod") or "—"
        line = Text()
        line.append(escape(f.get("subject") or ""), style="bold")
        line.append(" -", style="dim")
        line.append(escape(f.get("predicate") or ""), style="cyan")
        line.append("-> ", style="dim")
        line.append(escape(f.get("object") or ""))
        line.append("  ")
        line.append(mod, style=_MOD_STYLE.get(mod, "white"))
        line.append("\n")
        line.append("← ", style="dim")
        line.append(escape(f.get("source") or ""), style="dim cyan")
        grid.add_row(str(i), line)

    return Panel(
        grid,
        title=f"[bold]Facts[/]  [dim]{len(facts)}[/]",
        border_style="green",
        box=box.ROUNDED,
    )


def _tree_table(rows: list[dict[str, str]]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=False,
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("doc", style="cyan", ratio=2, overflow="ellipsis", no_wrap=True)
    table.add_column("chunks", justify="right", width=7)
    table.add_column("secs", justify="right", width=5)
    table.add_column("paths", ratio=3, overflow="fold")

    for i, r in enumerate(rows, 1):
        paths = r.get("paths") or ""
        # 过长路径压缩空白
        paths = re.sub(r"\s+", " ", paths)
        table.add_row(
            str(i),
            escape(r.get("doc") or ""),
            r.get("chunks") or "0",
            r.get("secs") or "0",
            escape(paths) if paths else "—",
        )

    return Panel(
        table,
        title=f"[bold]Evidence tree[/]  [dim]{len(rows)} docs[/]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _metrics_panel(metrics: dict[str, Any]) -> Panel:
    pairs: list[tuple[str, Any]] = list(metrics.get("pairs") or [])
    scalars = [(k, v) for k, v in pairs if not isinstance(v, dict)]
    dicts = [(k, v) for k, v in pairs if isinstance(v, dict)]

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()

    if scalars:
        chips = Text()
        for i, (k, v) in enumerate(scalars):
            if i:
                chips.append("   ", style="dim")
            chips.append(str(k), style="dim")
            chips.append("=")
            chips.append(str(v), style="bold bright_cyan")
        grid.add_row("metrics", chips)

    for key, data in dicts:
        grid.add_row(key, _format_mapping(data))

    title = "Metrics"
    if dicts and not scalars:
        title = dicts[0][0]
    elif any(k == "backends" for k, _ in dicts):
        title = "Context"
    return Panel(grid, title=f"[bold]{escape(title)}[/]", border_style="blue", box=box.ROUNDED)


def _signals_table(rows: list[dict[str, str]]) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("pri", width=4)
    table.add_column("type", style="cyan", width=22, overflow="ellipsis", no_wrap=True)
    table.add_column("payload", ratio=3, overflow="fold")
    table.add_column("n", justify="right", width=4)
    for r in rows:
        pri = r.get("priority") or ""
        style = "red" if pri == "P0" else "yellow" if pri == "P1" else "white"
        table.add_row(
            Text(pri, style=style),
            escape(r.get("type") or ""),
            escape(r.get("payload") or ""),
            r.get("n") or "0",
        )
    return Panel(
        table,
        title=f"[bold]Signals[/]  [dim]{len(rows)}[/]",
        border_style="yellow",
        box=box.ROUNDED,
    )


def _kgcl_table(rows: list[dict[str, str]]) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("op", style="cyan", width=20, no_wrap=True)
    table.add_column("body", ratio=3, overflow="fold")
    for i, r in enumerate(rows, 1):
        table.add_row(str(i), escape(r.get("op") or ""), escape(r.get("body") or ""))
    return Panel(
        table,
        title=f"[bold]KGCL[/]  [dim]{len(rows)}[/]",
        border_style="magenta",
        box=box.ROUNDED,
    )


def _arrows_table(rows: list[dict[str, str]]) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("from", style="bold", ratio=1, overflow="fold")
    table.add_column("", width=2, justify="center")
    table.add_column("to", style="cyan", ratio=2, overflow="fold")
    for i, r in enumerate(rows, 1):
        table.add_row(str(i), escape(r.get("left") or ""), "→", escape(r.get("right") or ""))
    # 右侧像 CURIE/ENT 时是 ER resolve；否则是通用映射（发版门禁等）
    resolve_like = all(
        bool(re.search(r"(?:HMD|DOC|CHK|ENT):", r.get("right") or "")) for r in rows
    )
    title = "Resolve" if resolve_like else "Mapping"
    return Panel(
        table,
        title=f"[bold]{title}[/]  [dim]{len(rows)}[/]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _spans_table(rows: list[dict[str, Any]]) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("span", style="bold", width=18, overflow="ellipsis", no_wrap=True)
    table.add_column("ms", justify="right", width=8)
    table.add_column("attrs", ratio=3, overflow="fold")
    for r in rows:
        attrs = r.get("attrs")
        attr_text = (
            _format_mapping(attrs)
            if isinstance(attrs, (dict, list, set, tuple))
            else Text(escape(str(attrs or "")), style="dim")
        )
        table.add_row(escape(str(r.get("name") or "")), f"{r.get('ms')}ms", attr_text)
    return Panel(
        table,
        title=f"[bold]Spans[/]  [dim]{len(rows)}[/]",
        border_style="dim",
        box=box.ROUNDED,
    )


def _candidates_table(rows: list[dict[str, str]]) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, expand=True, border_style="dim", pad_edge=False)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("candidate", style="cyan", ratio=2, overflow="ellipsis", no_wrap=True)
    table.add_column("score", justify="right", width=8)
    table.add_column("channel", width=12)
    for i, r in enumerate(rows, 1):
        table.add_row(
            str(i),
            escape(r.get("id") or ""),
            r.get("score") or "—",
            escape(r.get("channel") or ""),
        )
    return Panel(
        table,
        title=f"[bold]Candidates[/]  [dim]{len(rows)}[/]",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _decision_panel(row: dict[str, str]) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=8)
    grid.add_column()
    grid.add_row("stage", f"[bold]{escape(row.get('stage') or '')}[/]")
    grid.add_row("chosen", escape(row.get("chosen") or "—"))
    grid.add_row("conf", escape(row.get("conf") or "—"))
    grid.add_row("model", escape(row.get("model") or "—"))
    return Panel(grid, title="[bold]Decision[/]", border_style="yellow", box=box.ROUNDED)


def _quotes_panel(quotes: list[str]) -> Panel:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style="dim", width=2)
    grid.add_column()
    for q in quotes:
        grid.add_row("“", Text(escape(q), style="italic"))
    return Panel(
        grid,
        title=f"[bold]Quotes[/]  [dim]{len(quotes)}[/]",
        border_style="dim",
        box=box.ROUNDED,
    )


def _compare_panel(row: dict[str, str], *, passed: bool) -> Panel:
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    free = Text()
    free.append("free  ", style="dim")
    free.append_text(_styled_narrative(row.get("free") or "", passed=passed))
    paid = Text()
    paid.append("paid  ", style="dim")
    paid.append_text(_styled_narrative(row.get("paid") or "", passed=passed))
    grid.add_row(free, paid)
    title = escape(row.get("label") or "Compare")
    return Panel(grid, title=f"[bold]{title}[/]", border_style="dim", box=box.ROUNDED)


def _restore_panel(row: dict[str, str]) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right", min_width=10)
    grid.add_column()
    grid.add_row("chunk", f"[bold cyan]{escape(row.get('chunk') or '')}[/]")
    if row.get("breadcrumb"):
        grid.add_row("path", escape(row["breadcrumb"]))
    if row.get("page"):
        grid.add_row("pages", escape(row["page"]))
    if row.get("detail"):
        grid.add_row("expand", escape(row["detail"]))
    if row.get("meta"):
        grid.add_row("meta", escape(row["meta"]))
    return Panel(grid, title="[bold]Restore[/]", border_style="green", box=box.ROUNDED)


# --- narrative styling ---


def _styled_narrative(line: str, *, passed: bool) -> Text:
    base_style = "white" if passed else "yellow"
    text = Text()

    stripped = line.lstrip(" ")
    indent = len(line) - len(stripped)
    if indent:
        text.append(" " * min(indent, 6), style="dim")

    if stripped.startswith("候选 ") or stripped.startswith("decision["):
        text.append("▸ ", style="dim")
    elif stripped.startswith("span "):
        text.append("· ", style="dim")

    cursor = 0
    tokens: list[tuple[int, int, str]] = []
    for m in _MOD_LIST_RE.finditer(stripped):
        tokens.append((m.start(), m.end(), "modlist"))
    for m in _DICT_RE.finditer(stripped):
        tokens.append((m.start(), m.end(), "dict"))
    for m in _LIST_RE.finditer(stripped):
        # 跳过 modalities=[...]（已由 modlist 覆盖）与嵌在 dict 内的 list
        tokens.append((m.start(), m.end(), "list"))
    tokens.sort(key=lambda t: t[0])

    cleaned: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, kind in tokens:
        if start < last_end:
            continue
        cleaned.append((start, end, kind))
        last_end = end

    if not cleaned:
        _append_rich_fragment(text, stripped, base_style)
        return text

    for start, end, kind in cleaned:
        if start > cursor:
            _append_rich_fragment(text, stripped[cursor:start], base_style)
        chunk = stripped[start:end]
        if kind == "modlist":
            text.append("modalities=", style="dim")
            text.append(chunk[len("modalities=") :], style="bold magenta")
        else:
            text.append_text(_format_mapping(_safe_literal(chunk)))
        cursor = end
    if cursor < len(stripped):
        _append_rich_fragment(text, stripped[cursor:], base_style)
    return text


def _append_rich_fragment(text: Text, fragment: str, base_style: str) -> None:
    """模态词 + CURIE 上色。"""
    # 合并两种匹配按位置扫描
    marks: list[tuple[int, int, str, str]] = []
    for m in _MOD_WORD_RE.finditer(fragment):
        marks.append((m.start(), m.end(), "mod", m.group(1)))
    for m in _CURIE_RE.finditer(fragment):
        marks.append((m.start(), m.end(), "curie", m.group(1)))
    marks.sort(key=lambda t: t[0])

    cleaned: list[tuple[int, int, str, str]] = []
    last = -1
    for start, end, kind, val in marks:
        if start < last:
            continue
        cleaned.append((start, end, kind, val))
        last = end

    cursor = 0
    for start, end, kind, val in cleaned:
        if start > cursor:
            text.append(escape(fragment[cursor:start]), style=base_style)
        if kind == "mod":
            text.append(val, style=f"bold {_MOD_STYLE.get(val, 'white')}")
        else:
            text.append(escape(val), style="cyan")
        cursor = end
    if cursor < len(fragment):
        text.append(escape(fragment[cursor:]), style=base_style)


def _safe_literal(raw: str) -> Any:
    try:
        return ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return raw


def _format_mapping(data: Any) -> Text:
    """dict/list/set → chips。

    - list/set：逐项 chip（扩展词、语种、CURIE 集）
    - dict 且值全数值：key×N（模态 tally / 精度）
    - dict 且值含字符串：key=val（backends）
    """
    out = Text()
    if isinstance(data, (list, tuple, set, frozenset)):
        items = list(data)
        if not items:
            out.append("[]", style="dim")
            return out
        for i, item in enumerate(items):
            if i:
                out.append("  ", style="dim")
            s = str(item)
            style = "cyan" if ":" in s else "white"
            if s.upper() in _MOD_STYLE:
                style = f"bold {_MOD_STYLE[s.upper()]}"
            out.append(escape(s), style=style)
        return out

    if not isinstance(data, dict) or not data:
        out.append(escape(str(data)), style="dim")
        return out

    values = list(data.values())
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)

    first = True
    for key, val in data.items():
        if not first:
            out.append("  ", style="dim")
        first = False
        key_s = str(key)
        if numeric:
            style = _MOD_STYLE.get(key_s.upper(), "white")
            out.append(escape(key_s), style=f"bold {style}")
            out.append(f"×{val}", style="dim")
        else:
            out.append(f"{escape(key_s)}=", style="dim")
            val_s = str(val)
            style = _BACKEND_STYLE.get(val_s, "white")
            if style == "white":
                for prefix, st in _BACKEND_STYLE.items():
                    if val_s.startswith(prefix):
                        style = st
                        break
            if style == "white" and ":" in val_s:
                style = "cyan"
            out.append(escape(val_s), style=style)
    return out


# ---------------------------------------------------------------- helpers


def _surface_of(demo_id: str) -> str:
    if demo_id.startswith("W"):
        return "WorldModel"
    if demo_id.startswith("B"):
        return "Bridge"
    return "Literature"


def _surface_buckets(results: list[DemoResult]) -> list[tuple[str, list[DemoResult]]]:
    lit = [r for r in results if r.demo_id.startswith("D")]
    wm = [r for r in results if r.demo_id.startswith("W")]
    bridge = [r for r in results if r.demo_id.startswith("B")]
    return [("Literature", lit), ("WM", wm), ("Bridge", bridge)]


def _short_claim(claim: str, limit: int) -> str:
    claim = claim.strip()
    if len(claim) <= limit:
        return claim
    return claim[: max(0, limit - 1)].rstrip() + "…"
