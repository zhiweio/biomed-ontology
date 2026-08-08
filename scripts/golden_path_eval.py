#!/usr/bin/env python3
"""多 Golden Path 评估 CLI 入口。"""

from __future__ import annotations

import json
import sys

from biomed_ontology.foundation.golden_eval import DEFAULT_CANDIDATES, eval_golden_paths
from biomed_ontology.foundation.obs_log import configure_foundation_logging


def main(argv: list[str] | None = None) -> int:
    configure_foundation_logging(json_logs=True)
    args = list(argv if argv is not None else sys.argv[1:])
    summary = eval_golden_paths(args or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
