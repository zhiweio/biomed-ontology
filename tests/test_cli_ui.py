"""cli_ui：progress 静默与 Rich chrome helpers。"""

from __future__ import annotations

import io

from rich.console import Console

from biomed_ontology.cli_ui import (
    command_header,
    iter_progress,
    metrics_table,
    progress_disabled,
    tqdm_bar,
)


def test_progress_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("HMD_NO_PROGRESS", "1")
    assert progress_disabled() is True
    monkeypatch.delenv("HMD_NO_PROGRESS", raising=False)
    monkeypatch.setenv("TQDM_DISABLE", "true")
    assert progress_disabled() is True


def test_progress_disabled_force() -> None:
    assert progress_disabled(force=True) is True
    assert progress_disabled(force=False) is False


def test_iter_progress_disabled_passthrough() -> None:
    items = list(iter_progress(range(5), desc="x", disable=True))
    assert items == [0, 1, 2, 3, 4]


def test_tqdm_bar_disabled_update() -> None:
    with tqdm_bar(total=10, desc="x", disable=True) as bar:
        bar.update(3)
        bar.total = 10
        assert bar.n == 3


def test_command_header_and_metrics_table() -> None:
    buf = io.StringIO()
    cons = Console(file=buf, force_terminal=True, width=80, color_system=None)
    command_header("index", meta=[("mode", "full")], console=cons)
    metrics_table("摘要", [("chunks", "12")], console=cons)
    text = buf.getvalue()
    assert "index" in text
    assert "mode" in text
    assert "chunks" in text
    assert "12" in text
