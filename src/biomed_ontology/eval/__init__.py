"""双面 Eval：Identity（WM resolve）+ Literature（ARMS）+ Bridge。

与 ``hmd foundation golden-eval`` 互补而非重复：

- **本包**回答文献本体收益、引用忠实度、身份金标、跨面同 ENT
- **golden-eval** 回答 GraphDB / Milvus / OM 金路径是否诚实可达

编排入口：``run_dual_eval`` / ``DualEvalReport``。
"""

from __future__ import annotations

from biomed_ontology.eval.bridge import BridgeEval, eval_bridge
from biomed_ontology.eval.identity import IdentityEval, eval_identity
from biomed_ontology.eval.public_bios import PublicBiosEval, eval_public_bios
from biomed_ontology.eval.retrieval import (
    ARMS,
    ONTOLOGY_PROBES,
    SAPBERT_DELTA,
    VISUAL_BIO_DELTA,
    VISUAL_DELTA,
    ArmResult,
    NormalizationEval,
    RetrievalEval,
    eval_normalization,
    eval_retrieval,
    load_gold,
)
from biomed_ontology.eval.retrieval import (
    _chunk_key_index as _chunk_key_index,
)
from biomed_ontology.eval.stats import Significance, paired_significance
from biomed_ontology.eval.suite import ALL_SUITES, DualEvalReport, run_dual_eval

__all__ = [
    "ALL_SUITES",
    "ARMS",
    "ONTOLOGY_PROBES",
    "SAPBERT_DELTA",
    "VISUAL_BIO_DELTA",
    "VISUAL_DELTA",
    "ArmResult",
    "BridgeEval",
    "DualEvalReport",
    "IdentityEval",
    "NormalizationEval",
    "PublicBiosEval",
    "RetrievalEval",
    "Significance",
    "eval_bridge",
    "eval_identity",
    "eval_normalization",
    "eval_public_bios",
    "eval_retrieval",
    "load_gold",
    "paired_significance",
    "render_eval",
    "render_eval_compact",
    "run_dual_eval",
    "summary_json",
]


def render_eval(*args, **kwargs):
    from biomed_ontology.eval.render import render_eval as _render

    return _render(*args, **kwargs)


def render_eval_compact(*args, **kwargs):
    from biomed_ontology.eval.render import render_eval_compact as _render

    return _render(*args, **kwargs)


def summary_json(*args, **kwargs):
    from biomed_ontology.eval.render import summary_json as _summary

    return _summary(*args, **kwargs)
