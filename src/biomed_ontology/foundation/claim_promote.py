"""extracted → 人标 validated：只写 ontology/claims YAML，不 INSERT knowledge 边。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology.foundation.paths import CLAIMS_PATH, REPO_ROOT

__all__ = [
    "PROMOTIONS_DIR",
    "apply_approved_promotions",
    "approve_promotions",
    "list_extracted",
    "load_promotions",
    "save_promotions",
    "upsert_validated_claim",
]

PROMOTIONS_DIR = REPO_ROOT / "data" / "releases" / "claims"
PROMOTIONS_PATH = PROMOTIONS_DIR / "promotions.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_promotions(path: Path | None = None) -> tuple[Path, list[dict[str, Any]]]:
    dest = path or PROMOTIONS_PATH
    if not dest.is_file():
        return dest, []
    rows: list[dict[str, Any]] = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return dest, rows


def save_promotions(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8")


def list_extracted(*, extracted_path: Path | None = None) -> list[dict[str, Any]]:
    """Iceberg 不可达时读本地 extracted 清单。"""
    dest = extracted_path or (PROMOTIONS_DIR / "extracted.jsonl")
    if dest.is_file():
        rows = [
            json.loads(line)
            for line in dest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [r for r in rows if str(r.get("claim_status") or "extracted") == "extracted"]
    try:
        from biomed_ontology.lake.catalog import KNOWLEDGE_CLAIMS_TABLE, open_catalog

        table = open_catalog().load_table(KNOWLEDGE_CLAIMS_TABLE)
        scan = table.scan(row_filter="claim_status == 'extracted'").to_arrow()
        return [dict(zip(scan.column_names, row, strict=False)) for row in scan.to_pylist()]
    except Exception:
        return []


def approve_promotions(
    claim_ids: list[str],
    *,
    by: str,
    path: Path | None = None,
    extracted: list[dict[str, Any]] | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    dest, rows = load_promotions(path)
    by_id = {str(r.get("claim_id")): r for r in rows}
    now = _now()
    source = {str(r.get("claim_id")): r for r in (extracted or list_extracted())}
    approved: list[dict[str, Any]] = []
    for cid in claim_ids:
        row = by_id.get(cid) or dict(source.get(cid) or {"claim_id": cid})
        row["claim_id"] = cid
        row["status"] = "approved"
        row["approved_by"] = by
        row["approved_at"] = now
        by_id[cid] = row
        approved.append(row)
    save_promotions(dest, list(by_id.values()))
    return dest, approved


def upsert_validated_claim(
    claim: dict[str, Any],
    *,
    claims_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = claims_path or CLAIMS_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    raw = raw or {}
    claims = list(raw.get("claims") or [])
    cid = str(claim.get("claim_id") or "")
    record = {
        "claim_id": cid,
        "subject_id": claim.get("subject_id"),
        "predicate": claim.get("predicate"),
        "object_id": claim.get("object_id"),
        "object_value": claim.get("object_value"),
        "confidence": claim.get("confidence"),
        "claim_status": "validated",
        "evidence_ids": list(claim.get("evidence_ids") or []),
        "source_id": claim.get("source_id") or claim.get("document_id"),
        "extracted_by": claim.get("extracted_by") or "claim-promote",
        "span": claim.get("span") or "",
        "created_at": claim.get("created_at") or _now(),
        "promoted_by": claim.get("approved_by") or claim.get("promoted_by"),
        "promoted_at": _now(),
    }
    idx = next((i for i, c in enumerate(claims) if str(c.get("claim_id")) == cid), None)
    action = "update" if idx is not None else "append"
    if idx is None:
        claims.append(record)
    else:
        merged = dict(claims[idx])
        merged.update({k: v for k, v in record.items() if v is not None})
        merged["claim_status"] = "validated"
        claims[idx] = merged
    if dry_run:
        return {"path": str(path), "action": action, "claim_id": cid, "dry_run": True}
    raw["claims"] = claims
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"path": str(path), "action": action, "claim_id": cid, "dry_run": False}


def apply_approved_promotions(
    path: Path | None = None,
    *,
    claims_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    dest, rows = load_promotions(path)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        raise RuntimeError("claim_promote: no approved promotions (not an empty success)")
    written: list[dict[str, Any]] = []
    for row in approved:
        written.append(upsert_validated_claim(row, claims_path=claims_path, dry_run=dry_run))
        if not dry_run:
            row["status"] = "promoted"
            row["promoted_at"] = _now()
    if not dry_run:
        save_promotions(dest, rows)
    return {
        "dry_run": dry_run,
        "written": written,
        "approved_count": len(approved),
        "git_commit": False,
        "graph_insert": False,
    }
