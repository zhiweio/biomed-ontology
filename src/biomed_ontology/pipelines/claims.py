"""人批准的 claim 晋升：只写 YAML，再可选 sync。Prefect 不 INSERT knowledge 边。"""

from __future__ import annotations

from typing import Any

from prefect import flow, task

__all__ = ["claim_promote"]


@task
def task_apply_promotions(*, promotions: str | None, write: bool) -> dict[str, Any]:
    from pathlib import Path

    from biomed_ontology.foundation.claim_promote import apply_approved_promotions

    return apply_approved_promotions(
        Path(promotions) if promotions else None,
        dry_run=not write,
    )


@flow(name="claim_promote")
def claim_promote(
    *,
    promotions: str | None = None,
    write: bool = False,
    sync: bool = False,
) -> dict[str, Any]:
    """只消费 status=approved。无批准 → Failed。"""
    applied = task_apply_promotions(promotions=promotions, write=write)
    synced = None
    if write and sync:
        from biomed_ontology.pipelines.world_model import world_model_sync

        synced = world_model_sync()
    return {**applied, "sync": synced, "graph_insert": False}
