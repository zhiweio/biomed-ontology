"""多 Golden Path 评估（GraphDB+BIOS / Milvus / OM，禁止 YAML）。"""

from __future__ import annotations

from typing import Any

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.obs_log import get_logger
from biomed_ontology.foundation.world import load_world_model

__all__ = ["DEFAULT_CANDIDATES", "eval_golden_paths", "eval_one"]

DEFAULT_CANDIDATES = [
    "HMPL-504",
    "savolitinib",
    "AZD6094",
    "MET",
    "c-MET",
    "NSCLC",
]


def eval_one(api: FoundationApi, candidate: str) -> dict[str, Any]:
    result = api.golden_path(candidate)
    ctx = result.get("context") or {}
    backends = result.get("backends") or ctx.get("backends") or {}
    checks = {
        "ok": bool(result.get("ok")),
        "no_yaml": all(v != "yaml" for v in backends.values() if isinstance(v, str)),
        "backends_graphdb": backends.get("entity") == "graphdb"
        and backends.get("relationships") == "graphdb",
        "backends_milvus": backends.get("evidence") == "milvus",
        "backends_om": backends.get("assets") == "openmetadata",
        "bios_graphdb": bool(ctx.get("bios_bridges")),
        "evidence_nonempty": len(ctx.get("evidence") or []) > 0,
        "assets_nonempty": len(ctx.get("internal_assets") or []) > 0,
        "bios_backend": str(backends.get("bios") or "").startswith("graphdb_biomedical"),
    }
    return {
        "candidate": candidate,
        "passed": all(checks.values()),
        "checks": checks,
        "path": result.get("path"),
        "canonical_entity": result.get("canonical_entity"),
        "entity_kind": result.get("entity_kind"),
        "backends": backends,
        "counts": {
            "targets": len(ctx.get("targets") or []),
            "diseases": len(ctx.get("diseases") or []),
            "drugs": len(ctx.get("drugs") or []),
            "evidence": len(ctx.get("evidence") or []),
            "assets": len(ctx.get("internal_assets") or []),
            "bios": len(ctx.get("bios_bridges") or []),
        },
        "bios_bridges": ctx.get("bios_bridges") or [],
        "evaluation": result.get("evaluation") or {},
        "result": result,
    }


def eval_golden_paths(
    candidates: list[str] | None = None,
    *,
    api: FoundationApi | None = None,
) -> dict[str, Any]:
    log = get_logger("hmd.golden_eval")
    api = api or FoundationApi(load_world_model())
    rows = [eval_one(api, c) for c in (candidates or list(DEFAULT_CANDIDATES))]
    summary = {
        "total": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "failed": [r["candidate"] for r in rows if not r["passed"]],
        "paths": [
            {
                "candidate": r["candidate"],
                "passed": r["passed"],
                "checks": r["checks"],
                "path": r["path"],
                "canonical_entity": r["canonical_entity"],
                "entity_kind": r["entity_kind"],
                "backends": r["backends"],
                "counts": r["counts"],
                "bios_bridges": r["bios_bridges"],
            }
            for r in rows
        ],
    }
    log.info(
        "metrics",
        pillar="metrics",
        where="golden_path_eval",
        op="eval_suite",
        when={
            "total": summary["total"],
            "passed": summary["passed"],
            "failed_count": len(summary["failed"]),
        },
    )
    return summary
