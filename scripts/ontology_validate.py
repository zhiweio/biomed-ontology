#!/usr/bin/env python3
"""Ontology-as-Code 轻量校验：目录 / 映射 / 种子一致性；可选联调 Golden Path。

YAML 是离线资源：本脚本校验 seed，不把 YAML 当查询后端。
Golden Path 联调需 GraphDB+Milvus+OM（先 hmd foundation sync）。
"""

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


def check_golden_path_live() -> None:
    """查询必须走三后端；后端未就绪则跳过（非 YAML fallback）。"""
    from biomed_ontology.config import settings
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.graphdb import GraphDbClient
    from biomed_ontology.foundation.world import load_world_model

    if not GraphDbClient.from_settings().health():
        print("SKIP golden_path: GraphDB 未就绪（请 task foundation:up && hmd foundation sync）")
        return
    try:
        from pymilvus import MilvusClient

        if not MilvusClient(uri=settings.milvus_uri).has_collection("foundation_evidence"):
            print("SKIP golden_path: Milvus foundation_evidence 不存在（请 sync）")
            return
    except Exception as exc:
        print(f"SKIP golden_path: Milvus 不可用 ({exc})")
        return

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    api = FoundationApi(load_world_model(FOUNDATION))
    for mention, eid in expected["resolve_mentions"].items():
        out = api.resolve_entity(mention)
        hit = out["resolved"][0]
        if hit.get("canonical_entity") != eid:
            _fail(f"resolve({mention!r}) → {hit.get('canonical_entity')!r}，期望 {eid}")

    try:
        result = api.golden_path("HMPL-504")
    except Exception as exc:
        _fail(f"golden_path 必须读 GraphDB/Milvus/OM，失败：{exc}")
    if not result.get("ok"):
        _fail(f"golden_path 失败：{result}")
    backends = result.get("backends") or result.get("context", {}).get("backends") or {}
    for key, want in [
        ("entity", "graphdb"),
        ("relationships", "graphdb"),
        ("evidence", "milvus"),
        ("assets", "openmetadata"),
    ]:
        if backends.get(key) != want:
            _fail(f"backend[{key}]={backends.get(key)!r}，期望 {want}（禁止 YAML）")
    if any(v == "yaml" for v in backends.values() if isinstance(v, str)):
        _fail(f"禁止 YAML fallback，backends={backends}")
    ctx = result["context"]
    if not ctx.get("bios_bridges"):
        _fail("BIOS 桥接为空：请确认 GraphDB biomedical 已灌库（task foundation:init / bios-load）")
    if not str(backends.get("bios") or "").startswith("graphdb_biomedical"):
        _fail(f"bios backend 异常：{backends.get('bios')!r}")
    target_ids = {t["id"] for t in ctx.get("targets") or []}
    for t in expected["targets"]:
        if t["id"] not in target_ids:
            _fail(f"缺少 target {t['id']}")
    disease_ids = {d["id"] for d in ctx.get("diseases") or []}
    for d in expected["diseases"]:
        if d["id"] not in disease_ids:
            _fail(f"缺少 disease {d['id']}")
    evidence = ctx.get("evidence") or []
    if len(evidence) < expected["evidence_min_count"]:
        _fail("evidence 数量不足")
    asset_ids = {a.get("id") for a in ctx.get("internal_assets") or []}
    for aid in expected["internal_assets_contains"]:
        if aid not in asset_ids:
            _fail(f"缺少 internal_asset {aid}")
    print("golden_path live backends OK:", backends)


def main() -> None:
    check_tree()
    check_mappings_align_seed()
    check_claims()
    check_golden_path_live()
    print("ontology:validate OK")


if __name__ == "__main__":
    main()
