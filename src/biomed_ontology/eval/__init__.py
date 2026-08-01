"""P8 评测：归一化准确率 + 检索三臂对照。

三臂（纯 BM25 / 纯向量 / 本体增强混合）跑同一套 query 与同一份判定，
是为了让"本体到底带来多少收益"成为一个可复现的数字，
而不是一句"语义检索更好"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.pipeline import KnowledgeBase

__all__ = [
    "ARMS",
    "ArmResult",
    "NormalizationEval",
    "RetrievalEval",
    "eval_normalization",
    "eval_retrieval",
    "load_gold",
]

GOLD_DIR = Path(__file__).resolve().parents[3] / "data" / "gold"

# 三臂定义。名字直接写死通道组合，避免"混合"在不同报告里指代不同东西。
ARMS: dict[str, dict[str, Any]] = {
    "bm25_only": {
        "channels": (RetrievalChannelEnum.BM25,),
        "expand": False,
        "label": "纯 BM25（无本体）",
    },
    "dense_only": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "label": "纯向量（无本体）",
    },
    "ontology_hybrid": {
        "channels": (
            RetrievalChannelEnum.BM25,
            RetrievalChannelEnum.DENSE,
            RetrievalChannelEnum.GRAPH,
        ),
        "expand": True,
        "label": "本体增强混合",
    },
}


def load_gold(name: str, *, gold_dir: Path | None = None) -> dict[str, Any]:
    path = (gold_dir or GOLD_DIR) / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 归一化评测


@dataclass
class NormalizationEval:
    total: int
    correct: int
    by_entity_type: dict[str, tuple[int, int]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    ambiguous_total: int = 0
    ambiguous_correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def accuracy_by_type(self) -> dict[str, float]:
        return {k: (c / n if n else 0.0) for k, (c, n) in sorted(self.by_entity_type.items())}

    def as_table(self) -> str:
        lines = [f"归一化准确率 {self.accuracy:.1%}  ({self.correct}/{self.total})"]
        for k, v in self.accuracy_by_type().items():
            c, n = self.by_entity_type[k]
            lines.append(f"  {k:<12} {v:>6.1%}  ({c}/{n})")
        if self.ambiguous_total:
            acc = self.ambiguous_correct / self.ambiguous_total
            lines.append(
                f"  {'消歧':<12} {acc:>6.1%}  ({self.ambiguous_correct}/{self.ambiguous_total})"
            )
        for f in self.failures[:10]:
            lines.append(f"    ✗ {f['text']!r} 期望 {f['expect']} 实得 {f['got']}")
        return "\n".join(lines)


def eval_normalization(
    kb: KnowledgeBase, *, gold: dict[str, Any] | None = None
) -> NormalizationEval:
    from biomed_ontology.observability import TraceContext

    gold = gold or load_gold("normalization")
    ctx = TraceContext(trace_id="eval", ontology_release_id=kb.release_id)
    ev = NormalizationEval(total=0, correct=0)
    tally: dict[str, list[int]] = {}

    for case in gold["cases"]:
        et = case.get("entity_type")
        res = kb.normalizer.normalize(case["text"], ctx=ctx, entity_types=[et] if et else None)
        got = res.matched[0].concept_id if res.matched else None
        ok = got == case["expect"]
        ev.total += 1
        ev.correct += int(ok)
        bucket = tally.setdefault(et or "UNKNOWN", [0, 0])
        bucket[0] += int(ok)
        bucket[1] += 1
        if not ok:
            ev.failures.append({"text": case["text"], "expect": case["expect"], "got": got})

    for case in gold.get("ambiguous_cases") or []:
        res = kb.normalizer.normalize(case["text"], ctx=ctx, context=case.get("context"))
        got = res.matched[0].concept_id if res.matched else None
        ev.ambiguous_total += 1
        ev.ambiguous_correct += int(got == case["expect"])
        if got != case["expect"]:
            ev.failures.append(
                {
                    "text": f"{case['text']} @ {case.get('context', '')[:24]}",
                    "expect": case["expect"],
                    "got": got,
                }
            )

    ev.by_entity_type = {k: (v[0], v[1]) for k, v in tally.items()}
    return ev


# ---------------------------------------------------------------- 检索评测


@dataclass
class ArmResult:
    arm: str
    label: str
    recall_at_10: float
    precision_at_5: float
    ndcg_at_10: float
    mrr: float
    per_query: dict[str, float] = field(default_factory=dict)


@dataclass
class RetrievalEval:
    arms: dict[str, ArmResult]
    baseline: str = "bm25_only"
    target: str = "ontology_hybrid"

    def lift(self, metric: str = "recall_at_10") -> float:
        base = getattr(self.arms[self.baseline], metric)
        tgt = getattr(self.arms[self.target], metric)
        return (tgt - base) / base if base else float("inf")

    def as_table(self) -> str:
        head = f"{'臂':<16}{'Recall@10':>11}{'P@5':>9}{'nDCG@10':>10}{'MRR':>8}"
        lines = [head, "-" * len(head) * 2]
        for a in self.arms.values():
            lines.append(
                f"{a.label:<16}{a.recall_at_10:>11.3f}{a.precision_at_5:>9.3f}"
                f"{a.ndcg_at_10:>10.3f}{a.mrr:>8.3f}"
            )
        lines.append(f"\n本体增强 vs 纯 BM25 Recall@10 提升：{self.lift():+.1%}")
        return "\n".join(lines)


def _chunk_key_index(kb: KnowledgeBase) -> dict[str, str]:
    """`doc_id#section` → chunk_id。gold 用稳定键，运行时用哈希键，这里桥接。"""
    return {f"{c.doc_id}#{c.section}": c.chunk_id for c in kb.chunks}


def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def eval_retrieval(
    kb: KnowledgeBase,
    *,
    gold: dict[str, Any] | None = None,
    entitlements: frozenset[str] = frozenset(),
    top_k: int = 10,
) -> RetrievalEval:
    from biomed_ontology.observability import TraceContext
    from biomed_ontology.search import HybridSearcher

    gold = gold or load_gold("retrieval")
    index = _chunk_key_index(kb)
    searcher = HybridSearcher(kb)
    ctx = TraceContext(trace_id="eval", ontology_release_id=kb.release_id)

    cases = []
    for q in gold["queries"]:
        need = q.get("requires_entitlement")
        if need and need not in entitlements:
            # 无凭据时该 query 的正解不可见，计入会把"合规过滤"错算成"召回差"。
            continue
        rel = {index[k]: v for k, v in (q.get("relevant") or {}).items() if k in index}
        if rel:
            cases.append((q["id"], q["text"], rel))

    arms: dict[str, ArmResult] = {}
    for arm, cfg in ARMS.items():
        recalls, precisions, ndcgs, rrs, per_q = [], [], [], [], {}
        for qid, text, rel in cases:
            hits, _ = searcher.search(
                text,
                ctx=ctx,
                top_k=top_k,
                entitlements=entitlements,
                expand=cfg["expand"],
                channels=cfg["channels"],
            )
            ranked = [h.chunk_id for h in hits]
            found = [c for c in ranked if c in rel]
            recall = len(found) / len(rel)
            recalls.append(recall)
            per_q[qid] = recall
            precisions.append(sum(1 for c in ranked[:5] if c in rel) / 5)
            gains = [float(rel.get(c, 0)) for c in ranked]
            ideal = sorted((float(v) for v in rel.values()), reverse=True)[:top_k]
            idcg = _dcg(ideal)
            ndcgs.append(_dcg(gains) / idcg if idcg else 0.0)
            rrs.append(
                next(
                    (1 / (i + 1) for i, c in enumerate(ranked) if c in rel),
                    0.0,
                )
            )
        n = len(cases) or 1
        arms[arm] = ArmResult(
            arm=arm,
            label=cfg["label"],
            recall_at_10=sum(recalls) / n,
            precision_at_5=sum(precisions) / n,
            ndcg_at_10=sum(ndcgs) / n,
            mrr=sum(rrs) / n,
            per_query=per_q,
        )
    return RetrievalEval(arms=arms)
