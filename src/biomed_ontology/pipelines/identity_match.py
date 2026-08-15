"""离线 Zingg：materialize → docker train-link → export。生产禁止 stub。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from prefect import flow, task

__all__ = ["identity_match", "identity_match_dev"]


@task
def task_materialize(
    observations: Literal["lake", "bootstrap", "all"],
) -> dict[str, Any]:
    from biomed_ontology.foundation.zingg_io import materialize

    result = materialize(observations=observations)
    payload = {
        "enterprise_rows": result.enterprise_rows,
        "observation_rows": result.observation_rows,
        "sources": list(result.sources),
        "warnings": list(result.warnings),
        "enterprise_path": str(result.enterprise_path),
        "observation_path": str(result.observation_path),
    }
    if observations == "lake" and result.observation_rows == 0:
        raise RuntimeError(
            "identity_match: lake observations empty; do not fall back to bootstrap in production"
        )
    return payload


@task(tags=["zingg"])
def task_zingg_link(*, allow_stub: bool) -> dict[str, Any]:
    from biomed_ontology.foundation.zingg_io import link_stub_from_materialized, run_zingg_docker

    if allow_stub:
        path = link_stub_from_materialized()
        return {"mode": "stub", "raw": str(path)}
    run_zingg_docker()
    return {"mode": "docker", "raw": "data/zingg/raw_matches.jsonl"}


@task
def task_export_matches(min_score: float | None = None) -> dict[str, Any]:
    from biomed_ontology.foundation.zingg_io import export_matches

    return export_matches(min_score=min_score)


@task
def task_identity_smoke() -> dict[str, Any]:
    from biomed_ontology.eval.identity import eval_identity
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model

    ev = eval_identity(FoundationApi(load_world_model()))
    if not ev.gate_ok:
        raise RuntimeError(
            f"identity smoke failed: gate {ev.gate_correct}/{ev.gate_total} "
            f"failures={ev.failures[:5]}"
        )
    return {
        "accuracy": ev.accuracy,
        "gate_ok": ev.gate_ok,
        "gate_total": ev.gate_total,
        "total": ev.total,
    }


def _run_match(
    *,
    observations: Literal["lake", "bootstrap", "all"],
    allow_stub: bool,
    min_score: float | None,
    skip_smoke: bool,
) -> dict[str, Any]:
    from biomed_ontology.config import settings
    from biomed_ontology.foundation.zingg_io import (
        compute_zingg_input_fingerprint,
        load_zingg_input_fingerprint,
        save_zingg_input_fingerprint,
    )

    mat = task_materialize(observations)
    fp = compute_zingg_input_fingerprint(
        enterprise_path=Path(mat["enterprise_path"]),
        observation_path=Path(mat["observation_path"]),
        window_days=settings.zingg_window_days,
        observation_rows=int(mat["observation_rows"]),
    )
    prev = load_zingg_input_fingerprint()
    if prev == fp:
        link = {"mode": "skipped", "reason": "input fingerprint unchanged", "fingerprint": fp}
    else:
        link = task_zingg_link(allow_stub=allow_stub)
        save_zingg_input_fingerprint(fp)
        link = {**link, "fingerprint": fp}
    exported = task_export_matches(min_score)
    smoke = None if skip_smoke else task_identity_smoke()
    return {
        "materialize": mat,
        "link": link,
        "export": exported,
        "identity_smoke": smoke,
        "allow_stub": allow_stub,
        "fingerprint": fp,
    }


@flow(name="identity_match")
def identity_match(
    *,
    observations: Literal["lake", "bootstrap", "all"] = "lake",
    min_score: float | None = None,
    skip_smoke: bool = False,
) -> dict[str, Any]:
    """生产路径：docker 失败即 Failed，不 stub。"""
    return _run_match(
        observations=observations,
        allow_stub=False,
        min_score=min_score,
        skip_smoke=skip_smoke,
    )


@flow(name="identity_match_dev")
def identity_match_dev(
    *,
    observations: Literal["lake", "bootstrap", "all"] = "bootstrap",
    min_score: float | None = None,
    skip_smoke: bool = True,
) -> dict[str, Any]:
    """仅本地：允许 stub-link。"""
    from biomed_ontology.config import settings

    if settings.is_prod:
        raise RuntimeError("identity_match_dev is forbidden when HMD_ENV=prod")
    return _run_match(
        observations=observations,
        allow_stub=True,
        min_score=min_score,
        skip_smoke=skip_smoke,
    )


def run_zingg_link_for_cli(*, skip_docker: bool, compose: Path | None = None) -> str:
    """CLI ``zingg-run --mode full``：docker 失败可降 stub（仅 CLI，不是生产 flow）。"""
    from biomed_ontology.foundation.zingg_io import (
        link_stub_from_materialized,
        run_zingg_docker,
    )

    if skip_docker:
        link_stub_from_materialized()
        return "stub"
    try:
        run_zingg_docker(compose=compose)
        return "docker"
    except Exception:
        link_stub_from_materialized()
        return "stub_fallback"
