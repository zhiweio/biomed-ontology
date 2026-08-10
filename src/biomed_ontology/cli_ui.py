"""Shared CLI chrome: Rich header/summary + tqdm.rich progress."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Iterator, Sequence
from typing import TypeVar

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

__all__ = [
    "command_header",
    "console",
    "iter_progress",
    "metrics_table",
    "progress_disabled",
    "tqdm_bar",
]

T = TypeVar("T")

console = Console()


def progress_disabled(*, force: bool | None = None) -> bool:
    """True when progress bars should be silent (CI / pipes / env)."""
    if force is not None:
        return force
    if os.environ.get("TQDM_DISABLE", "").strip() in {"1", "true", "yes"}:
        return True
    if os.environ.get("HMD_NO_PROGRESS", "").strip() in {"1", "true", "yes"}:
        return True
    return not sys.stderr.isatty()


def command_header(
    title: str,
    *,
    meta: Sequence[tuple[str, str]] | None = None,
    console: Console | None = None,
) -> None:
    """Print a compact Panel announcing the command and key parameters."""
    out = console or globals()["console"]
    body = Text(title, style="bold")
    if meta:
        body.append("\n")
        for i, (key, value) in enumerate(meta):
            if i:
                body.append("\n")
            body.append(f"{key}: ", style="dim")
            body.append(escape(str(value)))
    out.print()
    out.print(
        Panel(
            body,
            box=box.ROUNDED,
            border_style="cyan",
            padding=(0, 1),
        )
    )
    out.print()


def metrics_table(
    title: str,
    rows: Sequence[tuple[str, str]],
    *,
    console: Console | None = None,
) -> None:
    """Two-column metrics summary (label / value)."""
    out = console or globals()["console"]
    table = Table(title=title, box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column("指标", style="dim")
    table.add_column("值", justify="right")
    for label, value in rows:
        table.add_row(label, value)
    out.print(table)


def iter_progress(
    iterable: Iterable[T],
    *,
    desc: str = "",
    total: int | None = None,
    disable: bool | None = None,
    unit: str = "it",
) -> Iterator[T]:
    """Yield from *iterable* with a ``tqdm.rich`` bar when enabled."""
    if progress_disabled(force=disable):
        yield from iterable
        return
    from tqdm.rich import tqdm as rich_tqdm

    yield from rich_tqdm(iterable, desc=desc, total=total, unit=unit)


def tqdm_bar(
    *,
    total: int | None = None,
    desc: str = "",
    disable: bool | None = None,
    unit: str = "it",
):
    """Manual tqdm.rich bar for callback-driven loops (``bar.update(n)``).

    When disabled, returns a no-op context manager with ``update`` / ``close``.
    """
    if progress_disabled(force=disable):
        return _NullBar()
    from tqdm.rich import tqdm as rich_tqdm

    return rich_tqdm(total=total, desc=desc, unit=unit)


class _NullBar:
    """Stand-in when progress is disabled; accepts the same surface as tqdm."""

    def __init__(self) -> None:
        self.total: int | None = None
        self.n: int = 0

    def __enter__(self) -> _NullBar:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def update(self, n: int = 1) -> None:
        self.n += n

    def close(self) -> None:
        return None

    def set_postfix_str(self, _: str, *, refresh: bool = True) -> None:
        return None
