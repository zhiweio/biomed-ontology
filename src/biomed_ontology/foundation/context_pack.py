"""Data-for-Agent Context Pack：推理吃 Pack，检索吃 Evidence。"""

from __future__ import annotations

from typing import Any

__all__ = ["CONTEXT_PACK_VERSION", "attach_pack_fields"]

CONTEXT_PACK_VERSION = "1.0"


def attach_pack_fields(
    payload: dict[str, Any],
    *,
    enterprise_id: str,
    entity: dict[str, Any] | None,
    evidence: list[Any] | None,
    assets: list[Any] | None,
    bios_bridges: list[Any] | None,
    found: bool,
) -> dict[str, Any]:
    """在既有 get_entity_context 载荷上挂 Pack 契约字段，不删旧键。"""
    missing: list[str] = []
    if not found:
        missing.append("entity")
    if found and not evidence:
        missing.append("evidence")
    if found and not assets:
        missing.append("assets")
    if found and not bios_bridges:
        missing.append("bios_bridges")

    payload["pack_version"] = CONTEXT_PACK_VERSION
    payload["identity"] = {
        "enterprise_id": enterprise_id,
        "entity_kind": (entity or {}).get("entity_kind") if entity else None,
        "preferred_label_en": (entity or {}).get("preferred_label_en") if entity else None,
        "ontology_release_id": payload.get("ontology_release_id"),
    }
    payload["license"] = {
        "policy": "candidate_generation",
        "note": "许可在候选期过滤；Pack 不编造缺失字段",
    }
    payload["evidence_tree"] = list(evidence or [])
    payload["missing"] = missing
    return payload
