#!/usr/bin/env python3
"""Convert Zingg link CSV output → HMD raw_matches.jsonl.

Zingg link assigns the same z_cluster to linked rows across data pipes;
z_source names the pipe (enterprise / observation). See:
https://docs.zingg.ai/latest/stepbystep/link.md
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def _load_rows(out_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    files = sorted(out_dir.rglob("*.csv"))
    if not files:
        # Spark may write part-* without .csv suffix
        files = sorted(p for p in out_dir.rglob("*") if p.is_file() and not p.name.startswith("_"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        # header sniff
        sample = text.splitlines()[0]
        if "z_cluster" not in sample and "z_zsource" not in sample and "z_source" not in sample:
            continue
        reader = csv.DictReader(text.splitlines())
        for row in reader:
            rows.append({k: (v or "").strip() for k, v in row.items() if k})
    return rows


def _source_name(row: dict[str, str]) -> str:
    # Zingg 0.6 link 输出列为 z_zsource（pipe name）
    return (row.get("z_zsource") or row.get("z_source") or "").lower()


def convert(out_dir: Path, raw_out: Path, model_id: str) -> int:
    rows = _load_rows(out_dir)
    by_cluster: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        cluster = row.get("z_cluster") or row.get("z_zid") or ""
        if not cluster:
            continue
        by_cluster[cluster].append(row)

    pairs: list[dict] = []
    for cluster, members in by_cluster.items():
        ents = [m for m in members if _source_name(m) == "enterprise"]
        obs = [m for m in members if _source_name(m) == "observation"]
        if not ents or not obs:
            # fallback: id starting with HMD:ENT: is enterprise
            ents = [m for m in members if str(m.get("id") or "").startswith("HMD:ENT:")]
            obs = [m for m in members if m not in ents]
        for o in obs:
            mention = o.get("label") or ""
            if not mention:
                continue
            score_raw = o.get("z_score") or ents[0].get("z_score") or "0"
            try:
                score = float(score_raw)
            except ValueError:
                score = 0.0
            for e in ents:
                eid = e.get("id") or ""
                if not eid.startswith("HMD:ENT:"):
                    continue
                pairs.append(
                    {
                        "mention": mention,
                        "enterprise_id": eid,
                        "score": score,
                        "source": "zingg",
                        "model_id": model_id,
                        "z_cluster": cluster,
                    }
                )

    # best score per mention
    best: dict[str, dict] = {}
    for p in pairs:
        key = p["mention"].strip().lower()
        prev = best.get(key)
        if prev is None or float(p["score"]) > float(prev["score"]):
            best[key] = p

    raw_out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(v, ensure_ascii=False) for v in best.values()]
    raw_out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"converted clusters={len(by_cluster)} pairs={len(best)} -> {raw_out}")
    return len(best)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zingg-out", type=Path, required=True)
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--model-id", default="1")
    args = ap.parse_args()
    n = convert(args.zingg_out, args.raw_out, args.model_id)
    if n == 0:
        print("WARN: no pairs converted (check z_source / header in raw_zingg)")


if __name__ == "__main__":
    main()
