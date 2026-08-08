from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.ingest.seed import build_from_seed, load_ambiguity_registry
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger
from biomed_ontology.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "data" / "seed"


@pytest.fixture(scope="session")
def registry():
    return load_registry()


@pytest.fixture(scope="session")
def seed_files() -> list[Path]:
    return sorted(p for p in SEED_DIR.glob("*.yaml") if p.name != "ambiguity.yaml")


@pytest.fixture(scope="session")
def ambiguity():
    return load_ambiguity_registry(SEED_DIR / "ambiguity.yaml")


@pytest.fixture
def ledgers(tmp_path: Path):
    return (
        IdLedger(tmp_path / "concept_ids.json", release="0.1.0"),
        SequenceLedger(tmp_path / "alias_ids.json", prefix="HMDA"),
    )


@pytest.fixture
def build(registry, seed_files, ambiguity, ledgers):
    """ledger 模式：专测旧 HMD:SUB 铸造（test_seed_build / test_ids）。"""
    id_ledger, alias_ledger = ledgers
    return build_from_seed(
        seed_files,
        registry=registry,
        id_ledger=id_ledger,
        alias_ledger=alias_ledger,
        ambiguity=ambiguity,
        id_mode="ledger",
    )


@pytest.fixture(scope="session")
def kb():
    """文献 KB（ENT）。session 级；默认不灌 GraphDB（图测见 test_graphstore_*）。"""
    from biomed_ontology.pipeline import build_literature_base

    return build_literature_base(with_graph=False)


@pytest.fixture
def api(kb):
    """每个测试一个新 ToolApi —— feedback_log 是可变状态，共用会互相污染。"""
    from biomed_ontology.tools import ToolApi

    return ToolApi.from_kb(kb)


@pytest.fixture
def ctx(kb):
    from biomed_ontology.observability import TraceContext

    return TraceContext(trace_id="test-trace", ontology_release_id=kb.release_id)


LICENSED = frozenset({"MOCK_LICENSED"})
