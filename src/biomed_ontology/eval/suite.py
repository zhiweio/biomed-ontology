"""双面 Eval 编排：Identity + Literature + Bridge（不吞并 golden-eval）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biomed_ontology.eval.bridge import BridgeEval, eval_bridge
from biomed_ontology.eval.identity import IdentityEval, eval_identity
from biomed_ontology.eval.retrieval import (
    NormalizationEval,
    RetrievalEval,
    eval_normalization,
    eval_retrieval,
)
from biomed_ontology.eval.targets import TargetOutcome, check_targets

__all__ = ["DualEvalReport", "run_dual_eval"]

SUITE_IDENTITY = "identity"
SUITE_LITERATURE = "literature"
SUITE_BRIDGE = "bridge"
ALL_SUITES = (SUITE_IDENTITY, SUITE_LITERATURE, SUITE_BRIDGE)


@dataclass
class DualEvalReport:
    """``hmd eval`` 统一报告。WM 三后端金路径见 ``hmd foundation golden-eval``。"""

    identity: IdentityEval | None = None
    normalization: NormalizationEval | None = None
    literature: RetrievalEval | None = None
    bridge: BridgeEval | None = None
    literature_targets: list[TargetOutcome] = field(default_factory=list)
    suites_run: list[str] = field(default_factory=list)

    @property
    def identity_ok(self) -> bool:
        return self.identity is None or self.identity.gate_ok

    @property
    def literature_ok(self) -> bool:
        if self.literature is None:
            return True
        # unavailable 臂（如 Milvus 未起）不拖垮整次 eval；只看已跑臂的 targets + 引用忠实度
        runnable = [o for o in self.literature_targets if not o.unavailable]
        targets_ok = (
            all((o.met or o.waived) and not o.stale_waiver for o in runnable) if runnable else True
        )
        citation_ok = all(a.citation_fidelity >= 1.0 for a in self.literature.arms.values())
        return targets_ok and citation_ok

    @property
    def bridge_ok(self) -> bool:
        return self.bridge is None or self.bridge.ok

    @property
    def ok(self) -> bool:
        return self.identity_ok and self.literature_ok and self.bridge_ok

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "suites_run": list(self.suites_run),
            "policy": {
                "world_model_gate": "hmd foundation golden-eval",
                "note": "eval 不跑 GraphDB/OM context；联调门禁见 golden-eval",
            },
        }
        if self.identity is not None:
            out["identity"] = {
                "accuracy": self.identity.accuracy,
                "gate_accuracy": self.identity.gate_accuracy,
                "gate_ok": self.identity.gate_ok,
                "total": self.identity.total,
                "correct": self.identity.correct,
                "gate_total": self.identity.gate_total,
                "gate_correct": self.identity.gate_correct,
                "failures": self.identity.failures,
            }
        if self.normalization is not None:
            out["normalization"] = {
                "accuracy": self.normalization.accuracy,
                "total": self.normalization.total,
                "correct": self.normalization.correct,
                "by_entity_type": {
                    k: {"correct": c, "total": n}
                    for k, (c, n) in self.normalization.by_entity_type.items()
                },
                "failures": self.normalization.failures[:20],
            }
        if self.literature is not None:
            out["literature"] = {
                "embedder": self.literature.embedder,
                "reranker": self.literature.reranker,
                "unavailable": self.literature.unavailable,
                "arms": {
                    name: {
                        "ndcg_at_10": arm.ndcg_at_10,
                        "recall_at_10": arm.recall_at_10,
                        "precision_at_5": arm.precision_at_5,
                        "mrr": arm.mrr,
                        "citation_fidelity": arm.citation_fidelity,
                        "query_count": arm.query_count,
                    }
                    for name, arm in self.literature.arms.items()
                },
                "targets": [
                    {
                        "id": o.target.id,
                        "met": o.met,
                        "waived": o.waived,
                        "stale_waiver": o.stale_waiver,
                        "unavailable": o.unavailable,
                        "observed": o.observed,
                        "actual": o.actual,
                    }
                    for o in self.literature_targets
                ],
            }
        if self.bridge is not None:
            out["bridge"] = {
                "ok": self.bridge.ok,
                "alias_passed": self.bridge.alias_passed,
                "alias_total": self.bridge.alias_total,
                "literature_passed": self.bridge.literature_passed,
                "literature_total": self.bridge.literature_total,
                "entitlement_ok": self.bridge.entitlement_ok,
                "failures": self.bridge.failures,
            }
        return out


def run_dual_eval(
    surface: Any,
    *,
    entitlements: frozenset[str] | None = None,
    milvus_backend: Any = None,
    embedder: str = "",
    reranker: Any = None,
    suites: tuple[str, ...] | list[str] | None = None,
) -> DualEvalReport:
    """编排 Identity / Literature / Bridge。不调用 ``eval_golden_paths``。"""
    wanted = tuple(suites) if suites else ALL_SUITES
    unknown = sorted(set(wanted) - set(ALL_SUITES))
    if unknown:
        raise ValueError(f"未知 suite {unknown}；可选：{list(ALL_SUITES)}")

    report = DualEvalReport(suites_run=list(wanted))
    ents = entitlements or frozenset()

    if SUITE_IDENTITY in wanted:
        report.identity = eval_identity(surface.foundation)

    if SUITE_LITERATURE in wanted:
        report.normalization = eval_normalization(surface.kb)
        report.literature = eval_retrieval(
            surface.kb,
            entitlements=ents,
            milvus_backend=milvus_backend,
            embedder=embedder or (milvus_backend.embedder.name if milvus_backend else ""),
            reranker=reranker,
        )
        report.literature_targets = check_targets(report.literature)

    if SUITE_BRIDGE in wanted:
        report.bridge = eval_bridge(surface, entitlements=ents)

    return report
