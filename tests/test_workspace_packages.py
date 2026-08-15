"""uv workspace 剖面：七个成员包可导入，瘦包不声明 torch/docling。"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_members_exist() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert root["tool"]["uv"]["workspace"]["members"] == ["packages/*"]
    names = {
        "hmd-contracts",
        "hmd-core",
        "hmd-ingest",
        "hmd-nlu",
        "hmd-kg",
        "hmd-index",
        "hmd-access",
    }
    found = {p.parent.name for p in (ROOT / "packages").glob("*/pyproject.toml")}
    assert names <= found


def test_hmd_nlu_deps_exclude_gpu_stack() -> None:
    data = tomllib.loads((ROOT / "packages" / "hmd-nlu" / "pyproject.toml").read_text())
    blob = " ".join(data["project"]["dependencies"]).casefold()
    for banned in ("torch", "docling", "mineru", "pymilvus", "flagembedding"):
        assert banned not in blob


def test_workspace_markers_importable() -> None:
    import hmd_access
    import hmd_contracts
    import hmd_core
    import hmd_index
    import hmd_ingest
    import hmd_kg
    import hmd_nlu

    assert hmd_nlu.OWNS
    assert hmd_contracts.OWNS
    assert hmd_core.OWNS
    assert hmd_ingest.OWNS
    assert hmd_kg.OWNS
    assert hmd_index.OWNS
    assert hmd_access.OWNS
