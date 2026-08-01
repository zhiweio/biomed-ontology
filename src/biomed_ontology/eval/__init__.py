"""P8 评测：归一化准确率 + 检索三臂对照。

三臂（纯 BM25 / 纯向量 / 本体增强混合）跑同一套 query 与同一份判定，
是为了让"本体到底带来多少收益"成为一个可复现的数字，
而不是一句"语义检索更好"。
"""

from __future__ import annotations

import math
import unicodedata
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

# 臂定义。名字直接写死通道与向量列组合，避免"混合"在不同报告里指代不同东西。
#
# `backend` 区分本地内存与 Milvus：后者不可达时该臂标为不可用，
# **不得回落到本地后端顶替** —— 那会让报告里的"Milvus 混合"其实是本地跑的。
ARMS: dict[str, dict[str, Any]] = {
    "bm25_only": {
        "channels": (RetrievalChannelEnum.BM25,),
        "expand": False,
        "backend": "local",
        "label": "纯 BM25（无本体）",
    },
    "dense_only": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "backend": "local",
        "label": "纯向量（无本体）",
    },
    "ontology_hybrid": {
        "channels": (
            RetrievalChannelEnum.BM25,
            RetrievalChannelEnum.DENSE,
            RetrievalChannelEnum.GRAPH,
        ),
        "expand": True,
        "backend": "local",
        "label": "本体增强混合",
    },
    # ---- 以下需要 Milvus。逐列拆开是为了让"SapBERT 值多少"成为一个减法。
    "milvus_lexical": {
        "channels": (RetrievalChannelEnum.BM25,),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("sparse_lexical",),
        "label": "Milvus 词法稀疏",
    },
    "milvus_general": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("dense_general",),
        "label": "Milvus 通用稠密",
    },
    "milvus_biomed": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("dense_biomed",),
        "label": "Milvus 生医稠密",
    },
    "milvus_hybrid_2col": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("sparse_lexical", "dense_general"),
        "label": "Milvus 双列混合",
    },
    "milvus_hybrid_3col": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("sparse_lexical", "dense_general", "dense_biomed"),
        "label": "Milvus 三列混合",
    },
    "ontology_hybrid_milvus": {
        "channels": (
            RetrievalChannelEnum.BM25,
            RetrievalChannelEnum.DENSE,
            RetrievalChannelEnum.GRAPH,
        ),
        "expand": True,
        "backend": "milvus",
        "vector_fields": ("sparse_lexical", "dense_general", "dense_biomed"),
        "label": "本体增强 + Milvus",
    },
}

# SapBERT 的净值 = 三列混合 − 双列混合。两臂唯一差别就是那一列，
# 差值之外没有别的解释，这是整套消融里唯一能支撑采购决策的数字。
SAPBERT_DELTA = ("milvus_hybrid_3col", "milvus_hybrid_2col")


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
    map_score: float = 0.0
    # 引用忠实度。不是可调的质量指标，而是硬约束：
    # 命中声称“这篇文档讲了这个概念”，它就必须真的讲了。
    # 低于 1.0 意味着系统在造引用 —— 这比召回低严重得多。
    citation_fidelity: float = 1.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    query_count: int = 0
    per_query: dict[str, float] = field(default_factory=dict)
    # 按语种拆分的同结构结果。SapBERT 是英文单语模型，
    # 只报总平均会把"英文涨了、中文没动甚至掉了"抹平成一个好看的数字。
    by_lang: dict[str, ArmResult] = field(default_factory=dict)


@dataclass
class RetrievalEval:
    arms: dict[str, ArmResult]
    baseline: str = "bm25_only"
    target: str = "ontology_hybrid"
    # 因后端不可达而未跑的臂。报告里必须显式列出 ——
    # 悄悄少几行会让读者以为那些配置没做，而不是没测。
    unavailable: dict[str, str] = field(default_factory=dict)
    # 跑 Milvus 臂时实际用的嵌入器。fake 下“生医稠密”列根本不是 SapBERT，
    # 不标出来的话那个净值会被当成模型结论转述出去。
    embedder: str = ""

    def lift(
        self,
        metric: str = "recall_at_10",
        *,
        target: str | None = None,
        baseline: str | None = None,
        lang: str | None = None,
    ) -> float:
        base = self._metric(baseline or self.baseline, metric, lang)
        tgt = self._metric(target or self.target, metric, lang)
        return (tgt - base) / base if base else float("inf")

    def _metric(self, arm: str, metric: str, lang: str | None) -> float:
        result = self.arms[arm]
        if lang is not None:
            result = result.by_lang[lang]
        return float(getattr(result, metric))

    def delta(self, metric: str = "recall_at_10", *, lang: str | None = None) -> float:
        """SapBERT 净值：三列 − 双列，绝对差而非比例。"""
        hi, lo = SAPBERT_DELTA
        if hi not in self.arms or lo not in self.arms:
            return float("nan")
        return self._metric(hi, metric, lang) - self._metric(lo, metric, lang)

    def as_table(self) -> str:
        lines = [self._block(None, "全部 query")]
        for lang in sorted({lg for a in self.arms.values() for lg in a.by_lang}):
            lines.append(self._block(lang, f"仅 {lang}"))
        lines.append(f"\n本体增强 vs 纯 BM25 Recall@10 提升：{self.lift():+.1%}")

        hi, lo = SAPBERT_DELTA
        if hi in self.arms and lo in self.arms:
            lines.append(
                f"\nSapBERT 净值（三列 − 双列，rerank 关闭，embedder={self.embedder or '?'}）："
            )
            for lang in [None, *sorted({lg for a in self.arms.values() for lg in a.by_lang})]:
                tag = lang or "全部"
                lines.append(f"  {tag:<6} Recall@10 {self.delta(lang=lang):+.3f}")
            if self.embedder not in {"sapbert", "dual"}:
                lines.append(
                    f"  ⚠ embedder={self.embedder or '?'} 并未加载 SapBERT，"
                    "上面的净值只验证链路贯通，不能用于回答“SapBERT 值不值得上”。"
                )

        if self.unavailable:
            lines.append("\n未运行的臂（后端不可达，非结果）：")
            lines.extend(f"  {arm:<24} {why}" for arm, why in sorted(self.unavailable.items()))

        broken = {
            a.label: a.citation_fidelity for a in self.arms.values() if a.citation_fidelity < 1
        }
        if broken:
            lines.append("\n引用忠实度破损（硬约束，不得低于 1.000）：")
            lines.extend(f"  {label:<24} {v:.3f}" for label, v in sorted(broken.items()))
        else:
            lines.append("\n引用忠实度：全部臂 1.000（无造引用）")
        return "\n".join(lines)

    def _block(self, lang: str | None, title: str) -> str:
        cols = f"{'Recall@10':>11}{'P@5':>9}{'nDCG@10':>10}{'MRR':>8}{'MAP':>8}{'P50ms':>8}{'n':>5}"
        rows = [f"【{title}】", _pad("臂", 20) + cols, "-" * (20 + len(cols))]
        for arm in self.arms.values():
            r = arm if lang is None else arm.by_lang.get(lang)
            if r is None:
                continue
            rows.append(
                _pad(arm.label, 20)
                + f"{r.recall_at_10:>11.3f}{r.precision_at_5:>9.3f}{r.ndcg_at_10:>10.3f}"
                + f"{r.mrr:>8.3f}{r.map_score:>8.3f}{r.latency_p50_ms:>8.1f}{r.query_count:>5d}"
            )
        return "\n".join(rows)


def _display_width(text: str) -> int:
    """CJK 字符占两列。按 len() 对齐会让中文表头整体错位，表就没法读了。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(1, width - _display_width(text))


def _chunk_key_index(kb: KnowledgeBase) -> dict[str, str]:
    """`doc_id#section` → chunk_id。gold 用稳定键，运行时用哈希键，这里桥接。"""
    return {f"{c.doc_id}#{c.section}": c.chunk_id for c in kb.chunks}


def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[idx]


@dataclass
class _QueryScore:
    qid: str
    lang: str
    recall: float
    precision: float
    ndcg: float
    rr: float
    ap: float
    elapsed_ms: float
    fidelity: float = 1.0


def _aggregate(arm: str, label: str, scores: list[_QueryScore]) -> ArmResult:
    n = len(scores) or 1
    return ArmResult(
        arm=arm,
        label=label,
        recall_at_10=sum(s.recall for s in scores) / n,
        precision_at_5=sum(s.precision for s in scores) / n,
        ndcg_at_10=sum(s.ndcg for s in scores) / n,
        mrr=sum(s.rr for s in scores) / n,
        map_score=sum(s.ap for s in scores) / n,
        citation_fidelity=sum(s.fidelity for s in scores) / n,
        latency_p50_ms=_percentile([s.elapsed_ms for s in scores], 0.50),
        latency_p95_ms=_percentile([s.elapsed_ms for s in scores], 0.95),
        query_count=len(scores),
        per_query={s.qid: s.recall for s in scores},
    )


def _citation_fidelity(kb: KnowledgeBase, hits: list[Any]) -> float:
    """每条命中都在声称"这篇文档讲了这些概念"。这里逐条回查语料证实。

    检索质量差只是找不到；引用不忠实是给出了看似有据的错误答案 ——
    后者在临床语境下代价高得多，所以它按硬约束报，不参与"够用就行"的权衡。

    真值取自语料本身（各切片实际挂的 concept_ids），而不是检索管线的输出，
    否则就成了拿管线的话验证管线自己。
    """
    from biomed_ontology.observability.contracts import citation_fidelity

    truth: dict[str, set[str]] = {}
    for chunk in kb.chunks:
        truth.setdefault(chunk.doc_id, set()).update(chunk.concept_ids)

    returned_docs = {h.doc_id: truth.get(h.doc_id, set()) for h in hits}
    claimed: list[tuple[str, str | None]] = []
    for hit in hits:
        claimed.extend((hit.doc_id, cid) for cid in hit.matched_concepts)
        if not hit.matched_concepts:
            claimed.append((hit.doc_id, None))
    return citation_fidelity(claimed, returned_docs)


def _score_one(ranked: list[str], rel: dict[str, int], *, top_k: int) -> tuple[float, ...]:
    found = [c for c in ranked if c in rel]
    recall = len(found) / len(rel)
    precision = sum(1 for c in ranked[:5] if c in rel) / 5
    gains = [float(rel.get(c, 0)) for c in ranked]
    ideal = sorted((float(v) for v in rel.values()), reverse=True)[:top_k]
    idcg = _dcg(ideal)
    ndcg = _dcg(gains) / idcg if idcg else 0.0
    rr = next((1 / (i + 1) for i, c in enumerate(ranked) if c in rel), 0.0)

    # MAP：命中位置越靠前越好，且对**每一个**相关项都计分。
    # MRR 只看第一条命中，在 8 条 query 的样本上抖动极大；MAP 更稳。
    hits, precision_sum = 0, 0.0
    for i, c in enumerate(ranked):
        if c in rel:
            hits += 1
            precision_sum += hits / (i + 1)
    ap = precision_sum / len(rel) if rel else 0.0
    return recall, precision, ndcg, rr, ap


def eval_retrieval(
    kb: KnowledgeBase,
    *,
    gold: dict[str, Any] | None = None,
    entitlements: frozenset[str] = frozenset(),
    top_k: int = 10,
    arms: dict[str, dict[str, Any]] | None = None,
    milvus_backend: Any = None,
    embedder: str = "",
) -> RetrievalEval:
    """跑消融。Milvus 臂需显式传入后端 —— 不可达时标记为未运行而非回落。

    回落到本地后端会让报告里的"Milvus 三列混合"其实是本地 TF-IDF 跑的，
    这种错误一旦进了采购决策文档就再也追不回来。
    """
    import time

    from biomed_ontology.observability import TraceContext
    from biomed_ontology.search import HybridSearcher

    gold = gold or load_gold("retrieval")
    index = _chunk_key_index(kb)
    searchers = {"local": HybridSearcher(kb)}
    if milvus_backend is not None:
        searchers["milvus"] = HybridSearcher(kb, backend=milvus_backend)
    ctx = TraceContext(trace_id="eval", ontology_release_id=kb.release_id)

    cases = []
    for q in gold["queries"]:
        need = q.get("requires_entitlement")
        if need and need not in entitlements:
            # 无凭据时该 query 的正解不可见，计入会把"合规过滤"错算成"召回差"。
            continue
        rel = {index[k]: v for k, v in (q.get("relevant") or {}).items() if k in index}
        if rel:
            cases.append((q["id"], q["text"], q.get("lang", "und"), rel))

    results: dict[str, ArmResult] = {}
    unavailable: dict[str, str] = {}

    for arm, cfg in (arms or ARMS).items():
        searcher = searchers.get(cfg.get("backend", "local"))
        if searcher is None:
            unavailable[arm] = f"{cfg.get('backend')} 后端未提供"
            continue

        scores: list[_QueryScore] = []
        for qid, text, lang, rel in cases:
            started = time.perf_counter()
            hits, _ = searcher.search(
                text,
                ctx=ctx,
                top_k=top_k,
                entitlements=entitlements,
                expand=cfg["expand"],
                channels=cfg["channels"],
                vector_fields=tuple(cfg.get("vector_fields", ())),
            )
            elapsed = (time.perf_counter() - started) * 1000
            recall, precision, ndcg, rr, ap = _score_one(
                [h.chunk_id for h in hits], rel, top_k=top_k
            )
            fidelity = _citation_fidelity(kb, hits)
            scores.append(
                _QueryScore(qid, lang, recall, precision, ndcg, rr, ap, elapsed, fidelity)
            )

        result = _aggregate(arm, cfg["label"], scores)
        for lang in sorted({s.lang for s in scores}):
            subset = [s for s in scores if s.lang == lang]
            result.by_lang[lang] = _aggregate(arm, cfg["label"], subset)
        results[arm] = result

    return RetrievalEval(arms=results, unavailable=unavailable, embedder=embedder)
