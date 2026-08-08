"""P2 Data Loop 脚手架：信号 → KGCL 候选落库；不做自动改本体。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.world import WorldModel, load_world_model

__all__ = ["EvolveMineResult", "mine_unmapped_candidates"]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "releases" / "foundation_candidates"


@dataclass
class EvolveMineResult:
    signals: int
    kgcl_path: Path
    json_path: Path


def mine_unmapped_candidates(
    texts: list[str],
    *,
    world: WorldModel | None = None,
    out_dir: Path | None = None,
) -> EvolveMineResult:
    """对一批查询跑 resolve；unmapped / 低置信写入候选文件。"""
    wm = world or load_world_model()
    api = FoundationApi(wm)
    dest = out_dir or DEFAULT_OUT
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidates: list[dict] = []
    kgcl_lines: list[str] = [
        f"# Foundation evolve-mine {stamp}",
        "# 人工策展后才可 apply；本阶段禁止自动写入 World Model",
        "",
    ]
    for text in texts:
        out = api.resolve_entity(text)
        for hit in out["resolved"]:
            if hit.get("canonical_entity") and hit.get("confidence", 0) >= 0.95:
                continue
            cand = {
                "mention": hit.get("mention") or text,
                "canonical_entity": hit.get("canonical_entity"),
                "external_ids": hit.get("external_ids") or [],
                "confidence": hit.get("confidence"),
                "resolution_method": hit.get("resolution_method"),
                "suggested_op": "create synonym"
                if not hit.get("canonical_entity")
                else "review mapping",
            }
            candidates.append(cand)
            mention = cand["mention"].replace('"', "")
            kgcl_lines.append(
                f'# create synonym "{mention}" for unresolved enterprise entity '
                f"(method={cand['resolution_method']})"
            )
            kgcl_lines.append(f'# TODO curate: "{mention}"')
            kgcl_lines.append("")

    kgcl_path = dest / f"{stamp}.kgcl"
    json_path = dest / f"{stamp}.candidates.json"
    kgcl_path.write_text("\n".join(kgcl_lines), encoding="utf-8")
    json_path.write_text(
        json.dumps({"generated_at": stamp, "candidates": candidates}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return EvolveMineResult(signals=len(candidates), kgcl_path=kgcl_path, json_path=json_path)
