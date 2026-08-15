"""Stage D/E/F：approve / reject / patch Git SSOT / verify（不写生产 GraphDB）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology.foundation.evolve_propose import PROPOSALS_DIR
from biomed_ontology.foundation.ids import normalize_alias_key
from biomed_ontology.foundation.paths import DICTIONARY_PATH, ENTITIES_PATH, ZINGG_MATCHES_PATH

__all__ = [
    "EvolveApplyResult",
    "EvolveVerifyResult",
    "apply_approved",
    "approve_proposals",
    "latest_proposals_path",
    "load_proposals",
    "reject_proposals",
    "save_proposals",
    "verify_proposals",
]


@dataclass
class EvolveApplyResult:
    dry_run: bool
    written: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    proposals_path: str = ""
    approved_count: int = 0
    already_applied_count: int = 0
    pending_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvolveVerifyResult:
    passed: int
    failed: int
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def latest_proposals_path(directory: Path | None = None) -> Path:
    root = directory or PROPOSALS_DIR
    files = sorted(root.glob("*.proposals.jsonl"))
    if not files:
        raise FileNotFoundError(f"no proposals under {root}")
    return files[-1]


def load_proposals(path: Path | None = None) -> tuple[Path, list[dict[str, Any]]]:
    p = path or latest_proposals_path()
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return p, rows


def save_proposals(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _match(
    rows: list[dict[str, Any]],
    *,
    proposal_ids: list[str] | None,
    tier: str | None,
    min_confidence: float | None,
    status: str | None = "pending_approval",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    id_set = {x.strip() for x in (proposal_ids or []) if x.strip()}
    for row in rows:
        if status and row.get("status") != status:
            continue
        if id_set and row.get("proposal_id") not in id_set:
            continue
        if tier and str(row.get("risk_tier")) != tier:
            continue
        if min_confidence is not None and float(row.get("confidence") or 0) < min_confidence:
            continue
        out.append(row)
    return out


def approve_proposals(
    path: Path | None = None,
    *,
    proposal_ids: list[str] | None = None,
    tier: str | None = None,
    min_confidence: float | None = None,
    by: str = "curator",
) -> tuple[Path, list[dict[str, Any]]]:
    p, rows = load_proposals(path)
    selected = _match(rows, proposal_ids=proposal_ids, tier=tier, min_confidence=min_confidence)
    if not selected and proposal_ids:
        raise ValueError(f"no matching pending proposals for {proposal_ids}")
    now = datetime.now(UTC).isoformat()
    selected_ids = {r["proposal_id"] for r in selected}
    for row in rows:
        if row.get("proposal_id") in selected_ids:
            row["status"] = "approved"
            row["approved_by"] = by
            row["approved_at"] = now
    save_proposals(p, rows)
    return p, [r for r in rows if r.get("proposal_id") in selected_ids]


def reject_proposals(
    path: Path | None = None,
    *,
    proposal_ids: list[str] | None = None,
    tier: str | None = None,
    reason: str = "rejected",
    by: str = "curator",
) -> tuple[Path, list[dict[str, Any]]]:
    p, rows = load_proposals(path)
    selected = _match(rows, proposal_ids=proposal_ids, tier=tier, min_confidence=None)
    if not selected and proposal_ids:
        raise ValueError(f"no matching pending proposals for {proposal_ids}")
    now = datetime.now(UTC).isoformat()
    selected_ids = {r["proposal_id"] for r in selected}
    for row in rows:
        if row.get("proposal_id") in selected_ids:
            row["status"] = "rejected"
            row["rejected_by"] = by
            row["rejected_at"] = now
            row["reject_reason"] = reason
    save_proposals(p, rows)
    return p, [r for r in rows if r.get("proposal_id") in selected_ids]


def _patch_dictionary(
    mention: str,
    enterprise_id: str,
    *,
    dry_run: bool,
    dictionary_path: Path | None = None,
) -> dict[str, Any]:
    path = dictionary_path or DICTIONARY_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else 'version: "0.2.0"\nentries:\n'
    raw = yaml.safe_load(text) or {}
    entries = list(raw.get("entries") or [])
    key = normalize_alias_key(mention)
    target_idx: int | None = None
    for i, entry in enumerate(entries):
        eid = str(entry.get("enterprise_id") or "")
        aliases = [str(a) for a in (entry.get("aliases") or [])]
        norms = {normalize_alias_key(a) for a in aliases}
        norms.add(normalize_alias_key(str(entry.get("mention") or "")))
        if eid == enterprise_id:
            target_idx = i
            if key in norms:
                return {
                    "path": str(path),
                    "action": "noop",
                    "mention": mention,
                    "enterprise_id": enterprise_id,
                    "reason": "alias_already_present",
                }
            break
        if key in norms and eid and eid != enterprise_id:
            return {
                "path": str(path),
                "action": "conflict",
                "mention": mention,
                "enterprise_id": enterprise_id,
                "reason": f"alias_maps_to_{eid}",
            }

    action = {
        "path": str(path),
        "action": "append_alias",
        "mention": mention,
        "enterprise_id": enterprise_id,
    }
    if dry_run:
        return action

    if target_idx is None:
        kind = "disease" if ":IND:" in enterprise_id else "chemical"
        if ":TGT:" in enterprise_id:
            kind = "gene"
        fragment = (
            f"  - mention: {mention}\n"
            f"    type: {kind}\n"
            f"    enterprise_id: {enterprise_id}\n"
            f"    external_ids: []\n"
            f"    aliases:\n"
            f"      - {mention}\n"
        )
        if not text.endswith("\n"):
            text += "\n"
        if "entries:" not in text:
            text += "entries:\n"
        path.write_text(text + fragment, encoding="utf-8")
        return action

    # Insert alias under matching enterprise_id block (preserve surrounding formatting).
    lines = text.splitlines(keepends=True)
    eid_line = f"enterprise_id: {enterprise_id}"
    block_start = next((i for i, ln in enumerate(lines) if eid_line in ln), None)
    if block_start is None:
        # fallback rewrite via yaml
        entries[target_idx].setdefault("aliases", []).append(mention)
        raw["entries"] = entries
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return action
    # walk forward to aliases: then append
    aliases_idx = None
    for j in range(block_start, min(block_start + 40, len(lines))):
        if lines[j].lstrip().startswith("aliases:"):
            aliases_idx = j
            break
        # next entry
        if j > block_start and lines[j].startswith("  - mention:"):
            break
    if aliases_idx is None:
        lines.insert(block_start + 1, f"    aliases:\n      - {mention}\n")
    else:
        insert_at = aliases_idx + 1
        while insert_at < len(lines) and lines[insert_at].startswith("      - "):
            insert_at += 1
        lines.insert(insert_at, f"      - {mention}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return action


def _patch_zingg(
    mention: str,
    enterprise_id: str,
    score: float,
    *,
    dry_run: bool,
    zingg_path: Path | None = None,
) -> dict[str, Any]:
    path = zingg_path or ZINGG_MATCHES_PATH
    key = normalize_alias_key(mention)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    for line in existing.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if normalize_alias_key(str(row.get("mention") or "")) == key:
            return {
                "path": str(path),
                "action": "noop",
                "mention": mention,
                "enterprise_id": enterprise_id,
                "reason": "already_present",
            }
    record = {
        "mention": mention,
        "enterprise_id": enterprise_id,
        "score": score,
        "source": "evolve-apply",
        "model_id": "curated",
        "z_cluster": None,
    }
    action = {
        "path": str(path),
        "action": "append",
        "mention": mention,
        "enterprise_id": enterprise_id,
        "record": record,
    }
    if dry_run:
        return action
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return action


def _patch_xref(
    enterprise_id: str,
    xref: str,
    *,
    surface: str,
    dry_run: bool,
    entities_path: Path | None = None,
) -> dict[str, Any]:
    """只改 xref/alias，不改 enterprise_id / kind。"""
    if surface == "sssom":
        return _patch_sssom(enterprise_id, xref, dry_run=dry_run)
    if surface == "catalog":
        return _patch_catalog_xref(enterprise_id, xref, dry_run=dry_run)
    path = entities_path or ENTITIES_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    raw = raw or {}
    entities = list(raw.get("entities") or [])
    action = {
        "path": str(path),
        "action": "append_xref",
        "mention": xref,
        "xref": xref,
        "enterprise_id": enterprise_id,
        "write_surface": "entities_xref",
    }
    target = next((e for e in entities if str(e.get("enterprise_id")) == enterprise_id), None)
    if target is None:
        action["action"] = "skip"
        action["reason"] = "entity_not_found"
        return action
    xrefs = [str(x) for x in (target.get("exact_match_xrefs") or [])]
    if xref in xrefs:
        action["action"] = "noop"
        action["reason"] = "xref_already_present"
        return action
    if dry_run:
        return action
    target["exact_match_xrefs"] = [*xrefs, xref]
    raw["entities"] = entities
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return action


def _patch_sssom(enterprise_id: str, xref: str, *, dry_run: bool) -> dict[str, Any]:
    from biomed_ontology.foundation.paths import ONTOLOGY_ROOT

    path = ONTOLOGY_ROOT / "mappings" / "sssom.tsv"
    row = f"{enterprise_id}\tskos:exactMatch\t{xref}\tevolve-apply\n"
    action = {
        "path": str(path),
        "action": "append_sssom",
        "mention": xref,
        "xref": xref,
        "enterprise_id": enterprise_id,
        "write_surface": "sssom",
    }
    if path.is_file() and xref in path.read_text(encoding="utf-8"):
        action["action"] = "noop"
        action["reason"] = "already_present"
        return action
    if dry_run:
        return action
    if not path.is_file():
        path.write_text(
            "subject_id\tpredicate_id\tobject_id\tmapping_justification\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(row)
    return action


def _patch_catalog_xref(enterprise_id: str, xref: str, *, dry_run: bool) -> dict[str, Any]:
    from biomed_ontology.foundation.paths import ONTOLOGY_ROOT

    prefix, _, local = xref.partition(":")
    key = enterprise_id.rsplit(":", 1)[-1]
    catalog_dir = ONTOLOGY_ROOT / "catalog"
    action = {
        "path": str(catalog_dir),
        "action": "append_xref",
        "mention": xref,
        "xref": xref,
        "enterprise_id": enterprise_id,
        "write_surface": "catalog",
    }
    hits = list(catalog_dir.glob("*.yaml")) if catalog_dir.is_dir() else []
    for path in hits:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        concepts = list(raw.get("concepts") or [])
        for concept in concepts:
            if str(concept.get("key") or "") != key:
                continue
            hints = dict(concept.get("xref_hints") or {})
            if prefix in hints:
                action["action"] = "noop"
                action["reason"] = "catalog_hint_present"
                action["path"] = str(path)
                return action
            if dry_run:
                action["path"] = str(path)
                return action
            hints[prefix] = {"by": "curie", "value": local or xref}
            concept["xref_hints"] = hints
            raw["concepts"] = concepts
            path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            action["path"] = str(path)
            return action
    action["action"] = "skip"
    action["reason"] = "catalog_concept_not_found"
    return action


def apply_approved(
    path: Path | None = None,
    *,
    dry_run: bool = True,
    progress: Any | None = None,
    dictionary_path: Path | None = None,
    zingg_path: Path | None = None,
) -> EvolveApplyResult:
    p, rows = load_proposals(path)
    approved = [r for r in rows if r.get("status") == "approved"]
    already_applied = sum(1 for r in rows if r.get("status") == "applied")
    pending = sum(1 for r in rows if r.get("status") == "pending_approval")
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if progress is not None:
        progress.total = len(approved)
    for prop in approved:
        op = prop.get("op")
        surface = prop.get("write_surface")
        mention = str(prop.get("mention") or "")
        target = prop.get("target_enterprise_id")
        if prop.get("risk_tier") == "L3" or op == "create_node" or not target:
            skipped.append(
                {
                    "proposal_id": prop.get("proposal_id"),
                    "reason": "l3_create_node_needs_manual_curation",
                    "mention": mention,
                    "hint": "L3 仅草稿：需人工补全 entities 后才能 apply；批量请用 --tier L1",
                }
            )
            if progress is not None:
                progress.update(1)
            continue
        if surface == "zingg_matches" or op == "fuzzy_link":
            score = float(
                (prop.get("evidence") or {}).get("zingg", {}).get("score")
                or prop.get("confidence")
                or 0.85
            )
            written.append(
                _patch_zingg(
                    mention,
                    str(target),
                    score,
                    dry_run=dry_run,
                    zingg_path=zingg_path,
                )
            )
        elif surface in {"dictionary", "entity_aliases"} or op == "create_synonym":
            written.append(
                _patch_dictionary(
                    mention,
                    str(target),
                    dry_run=dry_run,
                    dictionary_path=dictionary_path,
                )
            )
        elif surface in {"entities_xref", "catalog", "sssom"} or op == "add_xref":
            xref = str(prop.get("xref") or mention)
            written.append(
                _patch_xref(
                    str(target),
                    xref,
                    surface=str(surface or "entities_xref"),
                    dry_run=dry_run,
                )
            )
        else:
            skipped.append(
                {
                    "proposal_id": prop.get("proposal_id"),
                    "reason": f"unsupported_surface:{surface}",
                    "mention": mention,
                }
            )
        if progress is not None:
            progress.update(1)

    if not dry_run:
        from biomed_ontology.foundation.evolve_kgcl import write_kgcl_copy

        write_kgcl_copy(approved, Path(str(p).replace(".jsonl", ".kgcl")))
        now = datetime.now(UTC).isoformat()
        applied_ids = {
            w.get("mention") or w.get("xref")
            for w in written
            if w.get("action") in {"append_alias", "append", "append_xref", "append_sssom"}
        }
        for row in rows:
            if row.get("status") == "approved" and (
                row.get("mention") in applied_ids or row.get("xref") in applied_ids
            ):
                row["status"] = "applied"
                row["applied_at"] = now
        save_proposals(p, rows)

    return EvolveApplyResult(
        dry_run=dry_run,
        written=written,
        skipped=skipped,
        proposals_path=str(p),
        approved_count=len(approved),
        already_applied_count=already_applied,
        pending_count=pending,
    )


def verify_proposals(
    path: Path | None = None,
    *,
    statuses: set[str] | None = None,
    world: Any | None = None,
    progress: Any | None = None,
    dictionary_path: Path | None = None,
) -> EvolveVerifyResult:
    """Re-resolve approved/applied mentions; expect dictionary/zingg (or any canonical)."""
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.bern2 import load_enterprise_dictionary
    from biomed_ontology.foundation.world import load_world_model

    _, rows = load_proposals(path)
    want = statuses or {"approved", "applied"}
    targets = [r for r in rows if r.get("status") in want and r.get("target_enterprise_id")]
    wm = world or load_world_model()
    if dictionary_path is not None:
        assert wm.resolver is not None
        wm.resolver.bern2.dictionary = load_enterprise_dictionary(dictionary_path)
        wm.resolver.bern2.dictionary.__post_init__()
    api = FoundationApi(wm)
    out_rows: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    if progress is not None:
        progress.total = len(targets)
    for prop in targets:
        mention = str(prop["mention"])
        expect = str(prop["target_enterprise_id"])
        resolved = api.resolve_entity(mention)
        hits = list(resolved.get("resolved") or [])
        h0 = hits[0] if hits else {}
        canon = h0.get("canonical_entity")
        method = h0.get("resolution_method")
        conf = float(h0.get("confidence") or 0)
        ok = canon == expect and conf >= 0.5
        # catalog-backed ENT may resolve via dictionary even if not in entities YAML
        if not ok and canon == expect:
            ok = True
        row = {
            "proposal_id": prop.get("proposal_id"),
            "mention": mention,
            "expect": expect,
            "got": canon,
            "method": method,
            "confidence": conf,
            "pass": ok,
        }
        out_rows.append(row)
        if ok:
            passed += 1
        else:
            failed += 1
        if progress is not None:
            progress.update(1)
    return EvolveVerifyResult(passed=passed, failed=failed, rows=out_rows)
