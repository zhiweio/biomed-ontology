"""PR cheap 静态门：不依赖生产 GraphDB / Prefect Server。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_validate():
    path = ROOT / "scripts" / "ontology_validate.py"
    spec = importlib.util.spec_from_file_location("hmd_ontology_validate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    mod = _load_validate()
    mod.check_tree()
    mod.check_mappings_align_seed()
    mod.check_claims()

    from biomed_ontology.eval.extraction import eval_extraction
    from biomed_ontology.eval.identity import eval_identity
    from biomed_ontology.foundation.api import FoundationApi
    from biomed_ontology.foundation.world import load_world_model
    from biomed_ontology.identity import IdentityService
    from biomed_ontology.ontology.metrics import (
        load_metric_vocab,
        metric_codes_from_vocab,
        schema_metric_codes,
    )

    ev = eval_extraction(IdentityService.from_catalog().normalizer)
    if not ev.ok:
        print(f"FAIL extraction: f1={ev.f1} {ev.failures[:8]}", file=sys.stderr)
        raise SystemExit(1)

    ident = eval_identity(FoundationApi(load_world_model()))
    if not ident.gate_ok:
        print(f"FAIL identity gate: {ident.failures[:8]}", file=sys.stderr)
        raise SystemExit(1)

    vocab_codes = metric_codes_from_vocab(load_metric_vocab())
    schema_codes = schema_metric_codes()
    missing = sorted(vocab_codes - schema_codes)
    if missing:
        print(f"FAIL metric vocab not in LinkML MetricCode: {missing}", file=sys.stderr)
        raise SystemExit(1)

    print("ontology cheap CI OK")


if __name__ == "__main__":
    main()
