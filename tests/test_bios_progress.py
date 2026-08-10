"""initialize_bios on_progress callback."""

from __future__ import annotations

from pathlib import Path

from biomed_ontology.foundation import bios as bios_mod


def test_initialize_bios_on_progress(monkeypatch, tmp_path: Path) -> None:
    concepts = list(bios_mod.load_bios_subset_jsonl(bios_mod.DEFAULT_SUBSET))[:3]
    assert concepts

    monkeypatch.setattr(bios_mod, "read_bios_init_marker", lambda *_a, **_k: None)
    monkeypatch.setattr(bios_mod, "_bios_load_satisfied", lambda **_k: False)
    monkeypatch.setattr(bios_mod, "ensure_repository", lambda _c: None)
    monkeypatch.setattr(bios_mod, "_write_init_marker", lambda *_a, **_k: None)
    monkeypatch.setattr(
        bios_mod,
        "_stream_index_sqlite",
        lambda path, it: (it, path),
    )
    monkeypatch.setattr(
        bios_mod,
        "load_bios_subset_jsonl",
        lambda *_a, **_k: iter(concepts),
    )

    class _Graph:
        timeout = 60.0

        def health(self) -> bool:
            return True

        def clear_graph(self, *_a, **_k) -> None:
            return None

        def load_turtle(self, *_a, **_k) -> None:
            return None

    calls: list[int] = []
    result = bios_mod.initialize_bios(
        full=False,
        cache_dir=tmp_path,
        graphdb=_Graph(),  # ty: ignore[invalid-argument-type]
        force=True,
        on_progress=calls.append,
    )
    assert result["concepts"] == len(concepts)
    assert calls == list(range(1, len(concepts) + 1))
