#!/usr/bin/env python3
"""Ontology-as-Code 轻量校验：目录 / 映射 / Golden Path / 种子一致性。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = ROOT / "ontology"
SCHEMA = ROOT / "schema"
FOUNDATION = ROOT / "data" / "foundation"
EXPECTED = ONTOLOGY / "examples" / "golden_path" / "hmpl504" / "expected_context.json"


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def check_tree() -> None:
    required = [
        ONTOLOGY / "README.md",
        ONTOLOGY / "owl" / "README.md",
        ONTOLOGY / "shapes" / "README.md",
        ONTOLOGY / "mappings" / "bios.yaml",
        ONTOLOGY / "mappings" / "bern2.yaml",
        ONTOLOGY / "mappings" / "chebi.yaml",
        EXPECTED,
        SCHEMA / "hmd_enterprise.yaml",
        SCHEMA / "shapes" / "projection.shacl.ttl",
        SCHEMA / "generated" / "hmd_enterprise.owl.ttl",
        SCHEMA / "generated" / "hmd_enterprise.shacl.ttl",
    ]
    for path in required:
        if not path.exists():
            _fail(f"缺少必需路径：{path.relative_to(ROOT)}")


def check_mappings_align_seed() -> None:
    entities = yaml.safe_load((FOUNDATION / "enterprise_entities.yaml").read_text(encoding="utf-8"))
    by_id = {e["enterprise_id"]: e for e in entities.get("entities", [])}
    bios = yaml.safe_load((ONTOLOGY / "mappings" / "bios.yaml").read_text(encoding="utf-8"))
    for row in bios.get("mappings", []):
        eid = row["enterprise_id"]
        if eid not in by_id:
            _fail(f"bios.yaml 映射指向未知实体：{eid}")
        seed_xrefs = set(by_id[eid].get("exact_match_xrefs") or [])
        for xref in row.get("external_ids") or []:
            if xref not in seed_xrefs:
                _fail(f"{eid} 映射 {xref} 未出现在 enterprise_entities.exact_match_xrefs")


def check_claims() -> None:
    claims = yaml.safe_load((FOUNDATION / "knowledge_claims.yaml").read_text(encoding="utf-8"))
    for c in claims.get("claims", []):
        if c.get("predicate") == "supportedBy" and str(c.get("object_id", "")).startswith(
            "HMD:ENT:DC:"
        ):
            _fail(f"supportedBy 倒置：{c.get('claim_id')}")
        if c.get("predicate") == "testedIn" and c.get("subject_id", "").startswith("HMD:ENT:DC:"):
            break
    else:
        _fail("缺少 DrugCandidate testedIn Experiment claim")


def check_golden_path() -> None:
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    api = FoundationApi(load_world_model(FOUNDATION))
    for mention, eid in expected["resolve_mentions"].items():
        out = api.resolve_entity(mention)
        hit = out["resolved"][0]
        if hit.get("canonical_entity") != eid:
            _fail(f"resolve({mention!r}) → {hit.get('canonical_entity')!r}，期望 {eid}")

    result = api.golden_path("HMPL-504")
    if not result.get("ok"):
        _fail(f"golden_path 失败：{result}")
    if result["canonical_entity"] != expected["canonical_entity"]:
        _fail("canonical_entity 不匹配")
    ctx = result["context"]
    target_ids = {t["id"] for t in ctx.get("targets") or []}
    for t in expected["targets"]:
        if t["id"] not in target_ids:
            _fail(f"缺少 target {t['id']}")
        row = next(x for x in ctx["targets"] if x["id"] == t["id"])
        for xref in t.get("external_ids_contains") or []:
            if xref not in (row.get("external_ids") or []):
                _fail(f"target {t['id']} 缺少 external_id {xref}")
    disease_ids = {d["id"] for d in ctx.get("diseases") or []}
    for d in expected["diseases"]:
        if d["id"] not in disease_ids:
            _fail(f"缺少 disease {d['id']}")
    evidence = ctx.get("evidence") or []
    if len(evidence) < expected["evidence_min_count"]:
        _fail("evidence 数量不足")
    for field in expected["evidence_requires"]:
        if not any(e.get(field) for e in evidence):
            _fail(f"evidence 缺少字段 {field}")
    asset_ids = {a.get("id") for a in ctx.get("internal_assets") or []}
    for aid in expected["internal_assets_contains"]:
        if aid not in asset_ids:
            _fail(f"缺少 internal_asset {aid}")


def main() -> None:
    check_tree()
    check_mappings_align_seed()
    check_claims()
    check_golden_path()
    print("ontology:validate OK")


if __name__ == "__main__":
    main()
