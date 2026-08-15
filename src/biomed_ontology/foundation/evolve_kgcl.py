"""把已批准提案编译为可回放 KGCL 副本；执行仍走 YAML/JSONL 补丁。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from biomed_ontology.evolution import KgclOp
from biomed_ontology.foundation.ids import is_enterprise_id

__all__ = ["compile_proposal_kgcl", "write_kgcl_copy"]


def compile_proposal_kgcl(prop: dict[str, Any]) -> str:
    """L1/L2 必须是合法 KGCL 句，禁止 ``# TODO curate``。"""
    existing = str(prop.get("kgcl") or "").strip()
    if existing and "# TODO curate" not in existing:
        return existing
    mention = str(prop.get("mention") or "").replace("'", "")
    target = prop.get("target_enterprise_id")
    op = str(prop.get("op") or "")
    xref = str(prop.get("xref") or mention)
    if op == "add_xref" and target:
        return f"create exact match '{xref}' for {target}"
    if op in {"create_synonym", "fuzzy_link"} and target and is_enterprise_id(str(target)):
        qualifier = "exact" if op == "create_synonym" else "related"
        return KgclOp(
            "create synonym",
            str(target),
            new_value=mention,
            qualifier=qualifier,
            signal_id=str(prop.get("proposal_id") or ""),
            rationale=str(op),
        ).to_kgcl()
    if op == "create_node" or prop.get("risk_tier") == "L3":
        return KgclOp(
            "create node",
            f"NEW:{prop.get('mention_key') or mention}",
            new_value=mention,
            signal_id=str(prop.get("proposal_id") or ""),
            rationale="L3 draft; do not apply from flow",
        ).to_kgcl()
    return KgclOp(
        "create synonym",
        str(target or "NEW:unknown"),
        new_value=mention,
        qualifier="exact",
    ).to_kgcl()


def write_kgcl_copy(proposals: list[dict[str, Any]], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Foundation apply KGCL copy — executable L1/L2; L3 is draft only",
        "# SSOT remains proposals.jsonl; this file is the replayable human/tool copy",
        "",
    ]
    for prop in proposals:
        pid = prop.get("proposal_id") or ""
        lines.append(f"# {pid} status={prop.get('status')} tier={prop.get('risk_tier')}")
        lines.append(compile_proposal_kgcl(prop))
        lines.append("")
    dest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return dest
