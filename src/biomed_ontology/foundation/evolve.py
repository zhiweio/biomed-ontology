"""P2 Data Loop 脚手架：信号 → KGCL 候选落库；不做自动改本体。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.world import WorldModel, load_world_model

__all__ = ["EvolveMineResult", "HIGH_CONFIDENCE", "mine_unmapped_candidates"]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "releases" / "foundation_candidates"

# 已映射且置信度 ≥ 此阈值 → 不进候选（避免金标路径刷屏）
HIGH_CONFIDENCE = 0.95


@dataclass
class EvolveMineResult:
    signals: int
    kgcl_path: Path
    json_path: Path
    generated_at: str = ""
    queries: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kgcl_path"] = str(self.kgcl_path)
        data["json_path"] = str(self.json_path)
        data["policy"] = {
            "min_confidence_skip": HIGH_CONFIDENCE,
            "auto_apply": False,
            "note": "candidates only；人工策展后才可 apply",
        }
        return data


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
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    kgcl_lines: list[str] = [
        f"# Foundation evolve-mine {stamp}",
        "# 人工策展后才可 apply；本阶段禁止自动写入 World Model",
        f"# policy: skip if mapped AND confidence>={HIGH_CONFIDENCE}",
        "",
    ]
    for text in texts:
        out = api.resolve_entity(text)
        hits = list(out.get("resolved") or [])
        if not hits:
            cand = _candidate_from_hit(
                {
                    "mention": text,
                    "canonical_entity": None,
                    "external_ids": [],
                    "confidence": 0.0,
                    "resolution_method": "unmapped",
                },
                query=text,
            )
            candidates.append(cand)
            kgcl_lines.extend(_kgcl_stub(cand))
            continue
        for hit in hits:
            conf = float(hit.get("confidence") or 0.0)
            canon = hit.get("canonical_entity")
            if canon and conf >= HIGH_CONFIDENCE:
                skipped.append(
                    {
                        "mention": hit.get("mention") or text,
                        "query": text,
                        "canonical_entity": canon,
                        "confidence": conf,
                        "resolution_method": hit.get("resolution_method"),
                        "reason": f"mapped_high_confidence>={HIGH_CONFIDENCE}",
                    }
                )
                continue
            cand = _candidate_from_hit(hit, query=text)
            candidates.append(cand)
            kgcl_lines.extend(_kgcl_stub(cand))

    kgcl_path = dest / f"{stamp}.kgcl"
    json_path = dest / f"{stamp}.candidates.json"
    kgcl_path.write_text("\n".join(kgcl_lines).rstrip() + "\n", encoding="utf-8")
    payload = {
        "generated_at": stamp,
        "queries": list(texts),
        "candidates": candidates,
        "skipped": skipped,
        "policy": {
            "min_confidence_skip": HIGH_CONFIDENCE,
            "auto_apply": False,
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return EvolveMineResult(
        signals=len(candidates),
        kgcl_path=kgcl_path,
        json_path=json_path,
        generated_at=stamp,
        queries=list(texts),
        candidates=candidates,
        skipped=skipped,
    )


def _candidate_from_hit(hit: dict[str, Any], *, query: str) -> dict[str, Any]:
    canon = hit.get("canonical_entity")
    return {
        "mention": hit.get("mention") or query,
        "query": query,
        "canonical_entity": canon,
        "external_ids": list(hit.get("external_ids") or []),
        "confidence": hit.get("confidence"),
        "resolution_method": hit.get("resolution_method") or "unmapped",
        "suggested_op": "create synonym" if not canon else "review mapping",
    }


def _kgcl_stub(cand: dict[str, Any]) -> list[str]:
    mention = str(cand["mention"]).replace('"', "")
    op = cand.get("suggested_op") or "create synonym"
    method = cand.get("resolution_method") or "unmapped"
    canon = cand.get("canonical_entity")
    if op == "review mapping" and canon:
        header = (
            f'# review mapping "{mention}" → {canon} '
            f"(method={method}, conf={cand.get('confidence')})"
        )
    else:
        header = f'# create synonym "{mention}" for unresolved enterprise entity (method={method})'
    return [header, f'# TODO curate: "{mention}"', ""]
