"""本地跑通全部 Prefect 生产平面（``hmd pipeline`` / flow()）。

apply / claim-promote 用临时 approved 干跑，不写 Git、不 INSERT knowledge。
identity-match 走 ``--dev --observations bootstrap``（生产禁 stub）。
bios-bootstrap 默认 subset。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(args), flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    subprocess.run(args, check=True, cwd=ROOT, env=merged)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="hmd-pipeline-e2e-"))
    proposals = tmp / "approved.proposals.jsonl"
    promotions = tmp / "approved.promotions.jsonl"
    manifest = tmp / "batch.yaml"
    proposals.write_text(
        json.dumps(
            {
                "proposal_id": "HMDPROP:e2e-pipeline",
                "mention": "e2e-pipeline-alias",
                "op": "create_synonym",
                "write_surface": "dictionary",
                "target_enterprise_id": "HMD:ENT:DC:savolitinib",
                "risk_tier": "L1",
                "status": "approved",
                "confidence": 0.95,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    promotions.write_text(
        json.dumps(
            {
                "claim_id": "claim:e2e-pipeline",
                "subject_id": "HMD:ENT:DC:savolitinib",
                "predicate": "treats",
                "object_id": "HMD:ENT:IND:nsclc",
                "status": "approved",
                "evidence_ids": ["ev:e2e-pipeline"],
                "approved_by": "e2e",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir = tmp / "raw"
    raw_dir.mkdir()
    manifest.write_text(
        "documents:\n"
        "  - source_id: PUBMED\n"
        "    doc_id: DOC:LABEL.SAVO.SLICE\n"
        "    corpus_yaml: data/corpus/pipeline.yaml\n"
        "    register_asset: false\n",
        encoding="utf-8",
    )

    hmd = ["uv", "run", "hmd", "pipeline"]
    steps: list[list[str]] = [
        [*hmd, "ops-snapshot"],
        [*hmd, "eval", "--suite", "cheap"],
        [*hmd, "data-loop-mine"],
        [*hmd, "data-loop-enrich", "--no-llm"],
        [*hmd, "data-loop-apply", "--proposals", str(proposals)],
        [*hmd, "claim-promote", "--promotions", str(promotions)],
        [*hmd, "replay"],
        [*hmd, "catalog-publish"],
        [*hmd, "sync"],
        [*hmd, "identity-match", "--dev", "--observations", "bootstrap"],
        [*hmd, "literature-refresh", "--raw-dir", str(raw_dir)],
        [
            *hmd,
            "ingest",
            "--source",
            "PUBMED",
            "--doc-id",
            "DOC:LABEL.SAVO.SLICE",
            "--corpus-yaml",
            "data/corpus/pipeline.yaml",
            "--no-asset",
        ],
        [*hmd, "ingest-batch", "--manifest", str(manifest)],
        [*hmd, "bios-bootstrap", "--subset"],
        [*hmd, "slo-gate"],
    ]
    for args in steps:
        _run(args)
    print(f"pipeline e2e OK ({len(steps)} steps) tmp={tmp}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"FAIL: {' '.join(exc.cmd)} exit={exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
