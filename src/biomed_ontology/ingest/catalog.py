"""企业目录路径与仅-Normalizer 装配。lake ingest 走这里，不经 KnowledgeBase。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from biomed_ontology.ingest.seed import build_from_seed, load_ambiguity_registry
from biomed_ontology.normalize import Normalizer
from biomed_ontology.ontology.ids import SequenceLedger
from biomed_ontology.registry import load_registry

__all__ = [
    "DEFAULT_RELEASE",
    "ONTOLOGY_CATALOG",
    "catalog_files",
    "load_catalog_normalizer",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY_CATALOG = REPO_ROOT / "ontology" / "catalog"
DATA_ROOT = REPO_ROOT / "data"
DEFAULT_RELEASE = "0.3.0-ent"


def catalog_files(catalog_dir: Path | None = None) -> list[Path]:
    """仅 ``ontology/catalog/*.yaml``（ENT 目录 SSOT）。缺失或空目录硬失败。"""
    catalog = catalog_dir or ONTOLOGY_CATALOG
    if not catalog.is_dir():
        raise FileNotFoundError(f"ontology catalog 不存在：{catalog}")
    files = sorted(p for p in catalog.glob("*.yaml") if p.name != "ambiguity.yaml")
    if not files:
        raise FileNotFoundError(f"ontology catalog 无概念 YAML：{catalog}")
    return files


def load_catalog_normalizer(
    *,
    catalog_dir: Path | None = None,
    data_root: Path | None = None,
    release_id: str = DEFAULT_RELEASE,
    ledger_dir: Path | None = None,
) -> Normalizer:
    """从企业目录装配 ``Normalizer``，不建 corpus、不碰 GraphStore。"""
    catalog = catalog_dir or ONTOLOGY_CATALOG
    root = data_root or DATA_ROOT
    ledgers = ledger_dir or Path(tempfile.mkdtemp(prefix="hmd-catalog-ledger-"))
    ledgers.mkdir(parents=True, exist_ok=True)

    registry_path = root / "registry" / "sources.yaml"
    registry = load_registry(registry_path if registry_path.exists() else None)
    amb_path = catalog / "ambiguity.yaml"
    ambiguity = load_ambiguity_registry(amb_path) if amb_path.exists() else None

    built = build_from_seed(
        catalog_files(catalog),
        registry=registry,
        alias_ledger=SequenceLedger(ledgers / "alias_ids.json", prefix="HMDA"),
        ambiguity=ambiguity,
        id_mode="enterprise",
    )
    return Normalizer(
        concepts=built.concepts,
        synonyms=built.synonyms,
        ambiguity_index=ambiguity.norm_index() if ambiguity else {},
        release_id=release_id,
    )
