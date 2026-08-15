"""Context Pack 完整度：missing[] 必须与空槽一致，禁止编造字段。"""

from __future__ import annotations

from typing import Any

__all__ = ["eval_context_pack", "eval_context_packs"]

_SLOTS = ("entity", "evidence", "assets", "bios_bridges")


def eval_context_pack(pack: dict[str, Any]) -> dict[str, Any]:
    missing = list(pack.get("missing") or [])
    missing_set = set(missing)
    failures: list[str] = []
    found = bool(pack.get("found", "entity" not in missing_set))
    if not found and "entity" not in missing_set:
        failures.append("not found but missing omits entity")
    ident = pack.get("identity") or {}
    if not found and ident.get("preferred_label_en"):
        failures.append("fabricated preferred_label_en on missing entity")

    def _slot(*names: str) -> tuple[Any, bool]:
        for name in names:
            if name in pack:
                return pack[name], True
        return None, False

    evidence, has_ev = _slot("evidence_tree", "evidence")
    assets, has_assets = _slot("assets", "internal_assets")
    bios, has_bios = _slot("bios_bridges")
    slots = {
        "evidence": (evidence, has_ev),
        "assets": (assets, has_assets),
        "bios_bridges": (bios, has_bios),
    }
    if found:
        for name, (value, present) in slots.items():
            if not present:
                continue
            empty = not value
            if empty and name not in missing_set:
                failures.append(f"empty {name} not declared in missing")
            if (not empty) and name in missing_set:
                failures.append(f"{name} present but listed in missing")
    for name in missing:
        if name not in _SLOTS:
            failures.append(f"unknown missing slot {name}")
    return {"ok": not failures, "failures": failures, "missing": missing}


def eval_context_packs(packs: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [eval_context_pack(p) for p in packs]
    failures = [f for r in rows for f in r["failures"]]
    return {"ok": not failures, "failures": failures, "total": len(rows)}
