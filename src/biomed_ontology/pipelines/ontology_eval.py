"""评测合同：cheap / release 分面。T5 引用忠实度不可豁免。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prefect import flow, task

from biomed_ontology.pipeline import DEFAULT_RELEASE

__all__ = ["EVAL_DIR", "ontology_eval"]

EVAL_DIR = Path("data/releases/eval")
CHEAP_FACETS = ("validate", "identity", "extraction")
RELEASE_FACETS = (*CHEAP_FACETS, "literature", "bridge", "golden", "quality_gate")


def _write_scorecard(suite: str, payload: dict[str, Any]) -> Path:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = EVAL_DIR / f"{stamp}.{suite}.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


@task
def task_facet_validate() -> dict[str, Any]:
    import importlib.util

    from biomed_ontology.foundation.paths import REPO_ROOT

    path = REPO_ROOT / "scripts" / "ontology_validate.py"
    spec = importlib.util.spec_from_file_location("hmd_ontology_validate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.check_tree()
    mod.check_mappings_align_seed()
    mod.check_claims()
    return {"ok": True, "facet": "validate"}


@task
def task_facet_identity() -> dict[str, Any]:
    from biomed_ontology.eval.identity import eval_identity
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model

    ev = eval_identity(FoundationApi(load_world_model()))
    if not ev.gate_ok:
        raise RuntimeError(f"identity gate failed: {ev.failures[:8]}")
    return {
        "ok": True,
        "facet": "identity",
        "accuracy": ev.accuracy,
        "gate_ok": ev.gate_ok,
        "total": ev.total,
    }


@task
def task_facet_extraction() -> dict[str, Any]:
    from biomed_ontology.eval.extraction import eval_extraction
    from biomed_ontology.identity import IdentityService

    ev = eval_extraction(IdentityService.from_catalog().normalizer)
    if not ev.ok:
        raise RuntimeError(
            f"extraction contract failed f1={ev.f1} negation_ok={ev.negation_ok} {ev.failures[:8]}"
        )
    return {
        "ok": True,
        "facet": "extraction",
        "f1": ev.f1,
        "precision": ev.precision,
        "recall": ev.recall,
        "negation_ok": ev.negation_ok,
    }


@task(tags=["embed"])
def task_facet_literature() -> dict[str, Any]:
    from biomed_ontology.eval.suite import SUITE_BRIDGE, SUITE_LITERATURE, run_dual_eval
    from biomed_ontology.runtime import open_dual_surface

    report = run_dual_eval(open_dual_surface(), suites=(SUITE_LITERATURE, SUITE_BRIDGE))
    if not report.literature_ok:
        raise RuntimeError("literature contract failed (T5 citation fidelity is not waivable)")
    if not report.bridge_ok:
        failures = report.bridge.failures if report.bridge else []
        raise RuntimeError(f"bridge contract failed: {failures}")
    return {
        "ok": True,
        "facet": "literature+bridge",
        "literature_ok": report.literature_ok,
        "bridge_ok": report.bridge_ok,
        "report": report.to_dict(),
    }


@task
def task_facet_golden() -> dict[str, Any]:
    from biomed_ontology.foundation.golden_eval import eval_golden_paths

    summary = eval_golden_paths()
    if not summary.get("ok"):
        raise RuntimeError(f"golden-eval failed: {summary}")
    return {"ok": True, "facet": "golden", "summary": summary}


@task
def task_facet_quality_gate() -> dict[str, Any]:
    from biomed_ontology.pipeline import build_literature_base
    from biomed_ontology.quality import QualityGate

    kb = build_literature_base(with_graph=True)
    decision = QualityGate().evaluate(kb)
    if not decision.passed:
        raise RuntimeError(decision.explain())
    return {"ok": True, "facet": "quality_gate", "explain": decision.explain()}


@flow(name="ontology_eval")
def ontology_eval(*, suite: str = "cheap") -> dict[str, Any]:
    """cheap = validate+identity+extraction；release 再加 literature/bridge/golden/QualityGate。"""
    wanted = suite.strip().lower()
    if wanted not in {"cheap", "release", "validate", "identity", "extraction", "golden"}:
        raise ValueError(f"unknown eval suite {suite!r}")
    facets: dict[str, Any] = {}
    if wanted in {"cheap", "release", "validate"}:
        facets["validate"] = task_facet_validate()
    if wanted in {"cheap", "release", "identity"}:
        facets["identity"] = task_facet_identity()
    if wanted in {"cheap", "release", "extraction"}:
        facets["extraction"] = task_facet_extraction()
    if wanted == "release":
        facets["literature"] = task_facet_literature()
        facets["golden"] = task_facet_golden()
        facets["quality_gate"] = task_facet_quality_gate()
    if wanted == "golden":
        facets["golden"] = task_facet_golden()
    payload = {
        "ok": True,
        "suite": wanted,
        "ontology_release_id": DEFAULT_RELEASE,
        "facets": facets,
    }
    path = _write_scorecard(wanted, payload)
    payload["scorecard"] = str(path)
    return payload
