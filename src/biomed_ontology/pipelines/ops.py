"""值班新鲜度：读 fingerprint / quarantine / scorecard，对照 ops_slo.yaml。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from prefect import flow, task

from biomed_ontology.foundation.paths import REPO_ROOT
from biomed_ontology.lake.quarantine import load_open

__all__ = ["OPS_DIR", "ops_snapshot", "slo_gate"]

OPS_DIR = REPO_ROOT / "data" / "releases" / "ops"
SLO_PATH = REPO_ROOT / "ontology" / "policies" / "ops_slo.yaml"
EVAL_DIR = REPO_ROOT / "data" / "releases" / "eval"
WORLD_FP = REPO_ROOT / "data" / "cache" / "world_model_fingerprint.txt"
ZINGG_FP = REPO_ROOT / "data" / "cache" / "zingg_input_fingerprint.txt"


def load_slo_policy(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or SLO_PATH).read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _file_age_hours(path: Path) -> float | None:
    if not path.is_file():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return (datetime.now(UTC) - mtime).total_seconds() / 3600.0


def _latest_scorecard(suite: str) -> Path | None:
    if not EVAL_DIR.is_dir():
        return None
    matches = sorted(EVAL_DIR.glob(f"*.{suite}.json"))
    return matches[-1] if matches else None


def collect_ops_snapshot() -> dict[str, Any]:
    policy = load_slo_policy()
    open_rows = load_open()
    oldest = None
    for row in open_rows:
        seen = _parse_iso(str(row.get("first_seen") or ""))
        if seen is not None and (oldest is None or seen < oldest):
            oldest = seen
    oldest_age_h = (
        (datetime.now(UTC) - oldest).total_seconds() / 3600.0 if oldest is not None else 0.0
    )
    cheap = _latest_scorecard("cheap")
    release = _latest_scorecard("release")
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_version": policy.get("version"),
        "world_model_fingerprint_age_h": _file_age_hours(WORLD_FP),
        "world_model_fingerprint_present": WORLD_FP.is_file(),
        "zingg_input_fingerprint_age_h": _file_age_hours(ZINGG_FP),
        "open_quarantine_n": len(open_rows),
        "open_quarantine_oldest_age_h": round(oldest_age_h, 3),
        "cheap_scorecard": str(cheap) if cheap else None,
        "cheap_scorecard_age_h": _file_age_hours(cheap) if cheap else None,
        "release_scorecard": str(release) if release else None,
        "release_scorecard_age_h": _file_age_hours(release) if release else None,
        "er_unmapped_backlog": None,
        "note": "er_unmapped_backlog 需 Iceberg；缺测时不红此项",
    }


def evaluate_slo(snapshot: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    pol = policy or load_slo_policy()
    red: list[str] = []
    qpol = pol.get("ingest_quarantine") or {}
    max_docs = int(qpol.get("open_max_docs") or 50)
    max_age = float(qpol.get("open_max_age_hours") or 48)
    if int(snapshot.get("open_quarantine_n") or 0) > max_docs:
        red.append(f"open_quarantine {snapshot['open_quarantine_n']} > {max_docs}")
    if float(snapshot.get("open_quarantine_oldest_age_h") or 0) > max_age and int(
        snapshot.get("open_quarantine_n") or 0
    ):
        red.append(f"open_quarantine age {snapshot['open_quarantine_oldest_age_h']}h > {max_age}h")
    sync = pol.get("world_model_sync") or {}
    age = snapshot.get("world_model_fingerprint_age_h")
    if age is not None and float(age) > float(sync.get("max_age_hours") or 24):
        red.append(f"world_model_sync age {age}h over SLO")
    ev = pol.get("ontology_eval") or {}
    rel_age = snapshot.get("release_scorecard_age_h")
    if rel_age is not None and float(rel_age) > float(ev.get("release_max_age_hours") or 26):
        red.append(f"release scorecard age {rel_age}h over SLO")
    backlog = snapshot.get("er_unmapped_backlog")
    obs = pol.get("er_observations") or {}
    if backlog is not None and int(backlog) > int(obs.get("unmapped_backlog_max") or 500):
        red.append(f"er_observations backlog {backlog} over SLO")
    return {
        "ok": not red,
        "red": red,
        "rollback_lake": False,
    }


@task
def task_ops_snapshot() -> dict[str, Any]:
    snap = collect_ops_snapshot()
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = OPS_DIR / f"{stamp}.json"
    dest.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    snap["path"] = str(dest)
    return snap


@flow(name="ops_snapshot")
def ops_snapshot() -> dict[str, Any]:
    """写 Prefect artifact 口径的新鲜度快照；不回滚湖。"""
    return task_ops_snapshot()


@flow(name="slo_gate")
def slo_gate(*, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """红则 Failed；不回滚已入湖文档。"""
    snap = snapshot or collect_ops_snapshot()
    decision = evaluate_slo(snap)
    if not decision["ok"]:
        raise RuntimeError("slo_gate red: " + "; ".join(decision["red"]))
    return {**decision, "snapshot": snap}
