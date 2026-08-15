"""Scientific Data Loop：mine → enrich 停在提案；apply 只消费 approved。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefect import flow, task

__all__ = ["data_loop_apply", "data_loop_enrich", "data_loop_mine"]


@task
def task_mine(*, include_lake: bool, texts: list[str] | None) -> dict[str, Any]:
    from biomed_ontology.foundation.evolve import mine_unmapped_candidates

    result = mine_unmapped_candidates(list(texts or []), include_lake=include_lake)
    payload = result.to_dict()
    payload["auto_apply"] = False
    return payload


@task(tags=["llm"], timeout_seconds=180, retries=1)
def task_enrich(
    *,
    from_paths: list[str] | None,
    use_llm: bool,
    skip_tools: bool,
) -> dict[str, Any]:
    from biomed_ontology.foundation.evolve_propose import run_enrich

    paths = [Path(p) for p in from_paths] if from_paths else None
    result = run_enrich(from_paths=paths, use_llm=use_llm, skip_tools=skip_tools)
    out = result.to_dict()
    out["auto_apply"] = False
    out["status"] = "pending_approval"
    return out


@task
def task_apply(*, write: bool, proposals: str | None) -> dict[str, Any]:
    from biomed_ontology.foundation.evolve_apply import apply_approved, load_proposals

    path = Path(proposals) if proposals else None
    _, rows = load_proposals(path)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        raise RuntimeError("data_loop_apply: no approved proposals (not an empty success)")
    dry = apply_approved(path, dry_run=True)
    if not write:
        return {**dry.to_dict(), "phase": "dry_run"}
    written = apply_approved(path, dry_run=False)
    return {**written.to_dict(), "phase": "write"}


@task
def task_verify(proposals: str | None) -> dict[str, Any]:
    from biomed_ontology.foundation.evolve_apply import verify_proposals

    result = verify_proposals(Path(proposals) if proposals else None)
    payload = result.to_dict()
    if result.failed:
        raise RuntimeError(f"evolve-verify failed: {payload}")
    return payload


@flow(name="data_loop_mine")
def data_loop_mine(
    *,
    texts: list[str] | None = None,
    include_lake: bool = True,
) -> dict[str, Any]:
    """unmapped → candidates。不 apply。"""
    return task_mine(include_lake=include_lake, texts=texts)


@flow(name="data_loop_enrich")
def data_loop_enrich(
    *,
    from_paths: list[str] | None = None,
    use_llm: bool = True,
    skip_tools: bool = False,
) -> dict[str, Any]:
    """candidates → pending_approval 提案。到此结束。"""
    return task_enrich(from_paths=from_paths, use_llm=use_llm, skip_tools=skip_tools)


@flow(name="data_loop_apply")
def data_loop_apply(
    *,
    write: bool = False,
    proposals: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """只消费 approved。默认 dry-run；write 后 verify，再 catalog_publish + cheap eval。

    不 git commit。
    """
    applied = task_apply(write=write, proposals=proposals)
    verified = None
    published = None
    evaluated = None
    if write:
        verified = task_verify(proposals)
        if publish and applied.get("written"):
            from biomed_ontology.pipelines.ontology_eval import ontology_eval
            from biomed_ontology.pipelines.world_model import catalog_publish

            published = catalog_publish()
            evaluated = ontology_eval(suite="cheap")
    return {
        "apply": applied,
        "verify": verified,
        "catalog_publish": published,
        "eval": evaluated,
        "git_commit": False,
    }
