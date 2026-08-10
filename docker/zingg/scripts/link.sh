#!/usr/bin/env bash
# Placeholder Spark/Zingg link entrypoint.
# Real deployment: install Zingg on the Spark image and run link with config/link.json.
set -euo pipefail
echo "zingg-link skeleton: expect enterprise/observation under /work/data/zingg/input"
echo "Write scored pairs to /work/data/zingg/raw_matches.jsonl"
if [[ ! -f /work/data/zingg/raw_matches.jsonl ]]; then
  echo '[]' >/dev/null
  : >/work/data/zingg/raw_matches.jsonl
fi
echo "DONE (stub). Use hmd foundation zingg-run --mode stub-link for local pairs."
exit 0
