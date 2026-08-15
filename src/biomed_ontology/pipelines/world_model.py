"""World Model sync / BIOS 冷启动 / catalog 发布链。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from prefect import flow, task

from biomed_ontology.foundation.paths import (
    CLAIMS_PATH,
    DICTIONARY_PATH,
    ENTITIES_PATH,
    ONTOLOGY_ROOT,
    ZINGG_MATCHES_PATH,
)
from biomed_ontology.index_state import compute_catalog_fingerprint

__all__ = ["bios_bootstrap", "catalog_publish", "world_model_sync"]

_FP_PATH = Path("data/cache/world_model_fingerprint.txt")


def compute_world_model_fingerprint() -> str:
    """catalog + entities + validated claims。任一变了才该 sync。"""
    h = hashlib.sha256()
    h.update(compute_catalog_fingerprint().encode())
    for path in (ENTITIES_PATH, CLAIMS_PATH, DICTIONARY_PATH, ZINGG_MATCHES_PATH):
        h.update(path.name.encode())
        h.update(b"\0")
        if path.is_file():
            h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _load_fingerprint() -> str:
    if not _FP_PATH.is_file():
        return ""
    return _FP_PATH.read_text(encoding="utf-8").strip()


def _save_fingerprint(fp: str) -> None:
    _FP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FP_PATH.write_text(fp + "\n", encoding="utf-8")


@task(tags=["graphdb-replace"])
def task_sync_world_model() -> dict[str, Any]:
    """一次 replace 三个 seed sink；任一必选失败则 raise（不清 extracted）。"""
    from biomed_ontology.foundation.sync import sync_world_model

    result = sync_world_model(require_graphdb=True, require_milvus=True, require_om=True)
    if not (result.graphdb_ok and result.milvus_ok and result.om_ok):
        raise RuntimeError("world_model_sync incomplete: " + "; ".join(result.details))
    return result.to_dict()


@flow(name="world_model_sync")
def world_model_sync() -> dict[str, Any]:
    """seed 图 replace。不清 ``provenance_extracted``。"""
    from biomed_ontology.pipelines.preflight import probe_foundation

    probe_foundation()
    sync = task_sync_world_model()
    fp = compute_world_model_fingerprint()
    _save_fingerprint(fp)
    lineage = None
    try:
        from biomed_ontology.lake.om_governance import publish_run_lineage

        lineage = publish_run_lineage(
            pipeline="hmd.world_model_sync",
            from_fqn="ontology.catalog",
            to_fqn="openmetadata.glossary.HMDEnterpriseAssets",
            extra={"graph_uri": "http://asliva.example/graph/ontology", "fingerprint": fp},
        )
    except Exception as exc:
        lineage = {"ok": False, "error": str(exc)}
    return {
        **sync,
        "fingerprint": fp,
        "extracted_graph_cleared": False,
        "lineage": lineage,
    }


@task(tags=["graphdb-replace"])
def task_initialize_bios(*, force: bool = False, full: bool = True) -> dict[str, Any]:
    from biomed_ontology.foundation.bios import initialize_bios

    return initialize_bios(full=full, force=force)


@flow(name="bios_bootstrap")
def bios_bootstrap(*, force: bool = False, full: bool = True) -> dict[str, Any]:
    """冷启动：BIOS（可 skip）→ world_model_sync。日常不跑。"""
    bios = task_initialize_bios(force=force, full=full)
    sync = world_model_sync()
    return {"bios": bios, "sync": sync}


@flow(name="catalog_publish")
def catalog_publish(
    *,
    embedder_name: str = "multimodal-bio",
    rematerialize_zingg: bool = False,
) -> dict[str, Any]:
    """fingerprint 未变则 no-op；变了先 sync 再 literature incremental。

    无论是否 no-op，都对已映射 mention 回填 ``mapped`` 事件，让湖账本与词典对齐。
    """
    from biomed_ontology.foundation.er_backlog import close_mapped_er_observations

    er_close = close_mapped_er_observations()
    fp = compute_world_model_fingerprint()
    prev = _load_fingerprint()
    if prev == fp:
        return {
            "skipped": True,
            "reason": "world model fingerprint unchanged",
            "fingerprint": fp,
            "er_close": er_close,
        }

    sync = world_model_sync()
    from biomed_ontology.pipelines.literature import task_catalog_incremental

    incr = task_catalog_incremental(embedder_name)
    zingg: dict[str, Any] | None = None
    if rematerialize_zingg and ENTITIES_PATH.is_file():
        from biomed_ontology.pipelines.identity_match import identity_match

        zingg = identity_match()
    return {
        "skipped": False,
        "sync": sync,
        "literature_incremental": incr,
        "identity_match": zingg,
        "fingerprint": fp,
        "catalog_dir": str(ONTOLOGY_ROOT / "catalog"),
        "er_close": er_close,
    }
