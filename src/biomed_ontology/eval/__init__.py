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
from typing import TYPE_CHECKING, Any, NamedTuple

import yaml

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum
from biomed_ontology.eval.stats import Significance, paired_significance

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.pipeline import KnowledgeBase

__all__ = [
    "ARMS",
    "ONTOLOGY_PROBES",
    "ArmResult",
    "NormalizationEval",
    "RetrievalEval",
    "Significance",
    "eval_normalization",
    "eval_retrieval",
    "load_gold",
    "paired_significance",
]

GOLD_DIR = Path(__file__).resolve().parents[3] / "data" / "gold"

_ALL_CHANNELS = (
    RetrievalChannelEnum.BM25,
    RetrievalChannelEnum.DENSE,
    RetrievalChannelEnum.GRAPH,
)

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
        "channels": _ALL_CHANNELS,
        "expand": True,
        "backend": "local",
        "label": "本体增强混合",
    },
    # ---- 逐机制消融阶梯。这几行原先是手工跑出来贴进 README 的，谁也复现不了；
    # 升格为一等公民臂之后，"本体到底经由哪条路起作用、贡献是正是负"
    # 才是一个能被重跑、能被证伪的问题，而不是一段需要人记住出处的文字。
    #
    # 本体有三条互相独立的参与路径，必须逐条开：
    #   graph 通道（概念倒排）→ search-around（沿类型化链接多跳）→ 查询改写。
    # 一次全开时，任何变化都归因不到具体哪一条。
    "bm25_dense": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": False,
        "backend": "local",
        "label": "①BM25+DENSE 无本体",
    },
    "bm25_dense_graph": {
        "channels": _ALL_CHANNELS,
        "expand": False,
        "backend": "local",
        "label": "②+图通道（仅种子）",
    },
    "bm25_dense_hops": {
        "channels": _ALL_CHANNELS,
        "expand": True,
        "rewrite": False,
        "backend": "local",
        "label": "③+search-around",
    },
    "bm25_dense_expand": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": True,
        "backend": "local",
        "label": "④仅查询改写（无图）",
    },
    # ---- 交叉编码器精排。需显式传入 reranker，与 Milvus 臂同一套纪律：
    # 模型不在就标为未运行，**不得**悄悄退化成 NullReranker 顶替 ——
    # 那会让报表上的"+精排"其实是原序返回。
    #
    # 两臂缺一不可：只有 `ontology_hybrid_rerank` 时，涨了也说不清是本体的功劳
    # 还是精排的功劳。`bm25_rerank` 是那道减法里的被减数。
    "bm25_rerank": {
        "channels": (RetrievalChannelEnum.BM25,),
        "expand": False,
        "backend": "local",
        "rerank": True,
        "candidate_k": 50,
        "label": "⑤纯 BM25 + 精排",
    },
    "ontology_hybrid_rerank": {
        "channels": _ALL_CHANNELS,
        "expand": True,
        "backend": "local",
        "rerank": True,
        "candidate_k": 50,
        "label": "⑥本体增强 + 精排",
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
    "milvus_hybrid_4col": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": False,
        "backend": "milvus",
        "vector_fields": (
            "sparse_lexical",
            "dense_general",
            "dense_biomed",
            "dense_visual",
        ),
        "label": "Milvus 四列混合",
    },
    "milvus_hybrid_5col": {
        "channels": (RetrievalChannelEnum.BM25, RetrievalChannelEnum.DENSE),
        "expand": False,
        "backend": "milvus",
        "vector_fields": (
            "sparse_lexical",
            "dense_general",
            "dense_biomed",
            "dense_visual",
            "dense_visual_bio",
        ),
        "label": "Milvus 五列混合",
    },
    # 「我要看那张图」专用臂：只查视觉列，且只允许图像切片进候选。
    # `modality_intent` 让它**只在图像意图的 query 上评分** ——
    # 拿它去跑"呋喹替尼三线结直肠癌总生存期"只会得到一个 0.000，
    # 那个数字不回答任何问题，却会被当成"视觉列没用"转述出去。
    #
    # 通用塔与生医塔各一臂：两者的强项按图型分布（前者读图中文字与图表结构，
    # 后者认真实影像与镜检），只留一个就等于替读者先下了结论。
    "milvus_visual_only": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("dense_visual",),
        "modalities": ("IMAGE",),
        "modality_intent": "IMAGE",
        "label": "Milvus 视觉列（只看图）",
    },
    "milvus_visual_bio_only": {
        "channels": (RetrievalChannelEnum.DENSE,),
        "expand": False,
        "backend": "milvus",
        "vector_fields": ("dense_visual_bio",),
        "modalities": ("IMAGE",),
        "modality_intent": "IMAGE",
        "label": "Milvus 生医视觉列（只看图）",
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
# 同样的减法用在视觉列上：四列 − 三列。回答的是"混排场景下多这一列值不值"，
# 与 `milvus_visual_only` 回答的"只要图时它够不够准"是两个问题，不能互相顶替。
VISUAL_DELTA = ("milvus_hybrid_4col", "milvus_hybrid_3col")
# 生医视觉列的净值：五列 − 四列。分母是**已经有了通用视觉列**的配置 ——
# 要回答的不是"视觉有没有用"（那是 VISUAL_DELTA），而是"通用塔之外再加一个
# 生医专用塔，还能多拿到什么"。拿五列去减三列会把两条视觉列的贡献混记成一笔。
VISUAL_BIO_DELTA = ("milvus_hybrid_5col", "milvus_hybrid_4col")

# 本体敏感探针：别名归一 + 中文→英文桥接。企业实体身份桥接能力验收读这一集，
# 不读被图像意图与英文对照 query 稀释的全量 hybrid R@10。
ONTOLOGY_PROBES = ("bridge_zh", "alias")

# `ArmResult` 字段名 → `_QueryScore` 字段名。两侧命名不同是历史遗留
# （聚合值带 @K 后缀、逐条值不带），但对外只暴露一套 —— 就是 `ArmResult` 那套。
_PER_QUERY_KEYS = {
    "recall_at_10": "recall",
    "precision_at_5": "precision",
    "ndcg_at_10": "ndcg",
    "mrr": "rr",
    "map_score": "ap",
    "recall_at_pool": "pool_recall",
}


def _has_sapbert(name: str | None) -> bool:
    """净值这行字有没有资格被引用，取决于生医列到底是不是 SapBERT 算出来的。

    两种写法都要认：命令行别名（`dual`）和组合模型的真名
    （`bge-m3+sapbert+qwen3-vl`）。只认其中一种，就会在一份本来可信的报告上
    盖一个"数据不可信"的戳 —— 假阴性在这里和假阳性一样坏。
    """
    lowered = (name or "").lower()
    return "sapbert" in lowered or lowered in {"dual", "multimodal"}


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
    # 前十里有多少条是 gold 判过的。语料扩了而标注没跟上时，未判定的命中
    # 一律按“不相关”计入分母，召回会塌下来 —— 塌的是标注覆盖，不是检索质量。
    # 这一列就是用来分辨这两者的：它低，上面那些指标就只是下界。
    judged_at_10: float = 1.0
    # Recall@10 在这份 gold 上的天花板：mean(min(10, |rel|) / |rel|)。
    # 判定粒度是章节，一节动辄十几片，相关集比十个格子大得多 ——
    # 此时 Recall@10 就算检索器完美也到不了 1.0。
    # 不把这个数摆出来，0.42 会被读成"漏了 58%"，而其中大半是格子不够放。
    recall_ceiling: float = 1.0
    # 融合候选池（重排前）的召回。精排能做到的上限就是这个数 ——
    # 相关项没进池子，交叉编码器再准也够不着。只有显式设了 candidate_k 的臂有此值。
    recall_at_pool: float = 0.0
    pool_k: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    query_count: int = 0
    # qid → {指标名: 该 query 上的分数}。留逐条分数是为了做配对显著性检验：
    # 只有聚合均值时，"+0.013" 与"什么都没发生"在报表上无法区分。
    per_query: dict[str, dict[str, float]] = field(default_factory=dict)
    # 按语种拆分的同结构结果。SapBERT 是英文单语模型，
    # 只报总平均会把"英文涨了、中文没动甚至掉了"抹平成一个好看的数字。
    by_lang: dict[str, ArmResult] = field(default_factory=dict)
    # 按提问意图拆分（文本 25 条 / 图像 12 条）。混在一个平均里，
    # 检索侧改造的效果会被"文本检索答不了看图的问题"这部分常数项稀释，
    # 视觉列的效果反过来也会被 25 条文本 query 摊平 —— 两边都读不出来。
    by_intent: dict[str, ArmResult] = field(default_factory=dict)
    # 按探针拆分（bridge_zh / alias / hierarchy / control / image / license）。
    # 产品主 KPI 读 bridge_zh+alias；全量平均只作诊断。
    by_probe: dict[str, ArmResult] = field(default_factory=dict)

    def per_query_metric(self, metric: str) -> dict[str, float]:
        """取某个指标的逐 query 分数。`metric` 用 `ArmResult` 的字段名
        （`recall_at_10` / `ndcg_at_10` / ...），与 `lift()` / `delta()` 同一套口径 ——
        两处各用一套名字迟早会对不上。"""
        key = _PER_QUERY_KEYS.get(metric, metric)
        return {qid: scores[key] for qid, scores in self.per_query.items() if key in scores}


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
    # 精排臂实际用的重排模型。与 embedder 同理：报表上写着"本体增强+精排"
    # 而实际跑的是 NullReranker（原序返回），那张表就是在说谎。
    reranker: str = ""

    def lift(
        self,
        metric: str = "recall_at_10",
        *,
        target: str | None = None,
        baseline: str | None = None,
        lang: str | None = None,
        probes: tuple[str, ...] | None = None,
    ) -> float:
        base = self._metric(baseline or self.baseline, metric, lang, probes)
        tgt = self._metric(target or self.target, metric, lang, probes)
        return (tgt - base) / base if base else float("inf")

    def absolute_gain(
        self,
        metric: str = "ndcg_at_10",
        *,
        target: str | None = None,
        baseline: str | None = None,
        probes: tuple[str, ...] | None = None,
    ) -> float:
        """绝对增益（主 KPI 口径）。相对提升在基线很低时会被放大，这里不用。"""
        return self._metric(target or self.target, metric, None, probes) - self._metric(
            baseline or self.baseline, metric, None, probes
        )

    def _metric(
        self,
        arm: str,
        metric: str,
        lang: str | None,
        probes: tuple[str, ...] | None = None,
    ) -> float:
        return float(getattr(self._result(arm, lang, probes=probes), metric))

    def delta(
        self,
        metric: str = "recall_at_10",
        *,
        lang: str | None = None,
        pair: tuple[str, str] = SAPBERT_DELTA,
    ) -> float:
        """逐列净值：多一列 − 少一列，绝对差而非比例。"""
        hi, lo = pair
        if hi not in self.arms or lo not in self.arms:
            return float("nan")
        return self._metric(hi, metric, lang) - self._metric(lo, metric, lang)

    def significance(
        self,
        metric: str = "ndcg_at_10",
        *,
        target: str | None = None,
        baseline: str | None = None,
        lang: str | None = None,
        intent: str | None = None,
        probes: tuple[str, ...] | None = None,
    ) -> Significance:
        """配对显著性：target − baseline 的 95% CI 与置换检验 p 值。

        n=28 上任何不带区间的差值都不可解读。这个方法存在的意义就是让
        "本体增强涨了 0.013" 这句话必须连同 "CI 跨零、p=0.41" 一起说出口。
        """
        tgt = self._result(target or self.target, lang, intent, probes)
        base = self._result(baseline or self.baseline, lang, intent, probes)
        return paired_significance(tgt.per_query_metric(metric), base.per_query_metric(metric))

    def _result(
        self,
        arm: str,
        lang: str | None,
        intent: str | None = None,
        probes: tuple[str, ...] | None = None,
    ) -> ArmResult:
        result = self.arms[arm]
        if probes:
            # 多探针取并集后微平均；单探针走 by_probe。
            if len(probes) == 1:
                return result.by_probe[probes[0]]
            scores: dict[str, dict[str, float]] = {}
            for p in probes:
                sub = result.by_probe.get(p)
                if sub is None:
                    continue
                scores.update(sub.per_query)
            if not scores:
                return ArmResult(
                    arm=arm,
                    label=result.label,
                    recall_at_10=0.0,
                    precision_at_5=0.0,
                    ndcg_at_10=0.0,
                    mrr=0.0,
                )
            n = len(scores) or 1
            return ArmResult(
                arm=arm,
                label=result.label,
                recall_at_10=sum(s.get("recall", 0.0) for s in scores.values()) / n,
                precision_at_5=sum(s.get("precision", 0.0) for s in scores.values()) / n,
                ndcg_at_10=sum(s.get("ndcg", 0.0) for s in scores.values()) / n,
                mrr=sum(s.get("rr", 0.0) for s in scores.values()) / n,
                map_score=sum(s.get("ap", 0.0) for s in scores.values()) / n,
                query_count=len(scores),
                per_query=scores,
            )
        if intent is not None:
            result = result.by_intent[intent]
        return result if lang is None else result.by_lang[lang]

    def as_table(self) -> str:
        lines = [self._block(None, "全部 query")]
        # 主 KPI 切片靠前：读表的人不应先被全量 +0.8% 带偏。
        if any(ONTOLOGY_PROBES[0] in a.by_probe for a in self.arms.values()):
            lines.append(
                self._probes_block(ONTOLOGY_PROBES, "本体敏感探针（bridge_zh + alias，主 KPI）")
            )
        for probe in sorted({p for a in self.arms.values() for p in a.by_probe}):
            lines.append(self._block(probe, f"探针 {probe}", axis="by_probe"))
        for intent in sorted({i for a in self.arms.values() for i in a.by_intent}):
            lines.append(self._block(intent, f"意图 {intent}", axis="by_intent"))
        for lang in sorted({lg for a in self.arms.values() for lg in a.by_lang}):
            lines.append(self._block(lang, f"仅 {lang}"))
        if any(ONTOLOGY_PROBES[0] in a.by_probe for a in self.arms.values()):
            gain = self.absolute_gain(probes=ONTOLOGY_PROBES)
            lines.append(f"\n本体敏感探针 nDCG@10 绝对增益：{gain:+.3f}（主 KPI）")
        lines.append(f"全量 Recall@10 相对提升：{self.lift():+.1%}（诊断，含图像/对照稀释）")
        lines.extend(self._significance_block())
        lines.extend(self._pool_note())

        hi, lo = SAPBERT_DELTA
        if hi in self.arms and lo in self.arms:
            # rerank 的开关状态必须跟着数字走：这两臂都不开精排，但整份报告里
            # 可能有开精排的臂，写死一句"rerank 关闭"迟早变成一句假话。
            lines.append(
                f"\nSapBERT 净值（三列 − 双列，rerank={self.reranker or '关闭'}，"
                f"embedder={self.embedder or '?'}）："
            )
            for lang in [None, *sorted({lg for a in self.arms.values() for lg in a.by_lang})]:
                tag = lang or "全部"
                lines.append(f"  {tag:<6} Recall@10 {self.delta(lang=lang):+.3f}")
            if not _has_sapbert(self.embedder):
                lines.append(
                    f"  ⚠ embedder={self.embedder or '?'} 并未加载 SapBERT，"
                    "上面的净值只验证链路贯通，不能用于回答“SapBERT 值不值得上”。"
                )

        for pair, title in (
            (VISUAL_DELTA, "视觉列净值（四列 − 三列，混排场景）："),
            (VISUAL_BIO_DELTA, "生医视觉列净值（五列 − 四列，已有通用视觉列之上）："),
        ):
            vhi, vlo = pair
            if vhi not in self.arms or vlo not in self.arms:
                continue
            lines.append(f"\n{title}")
            for lang in [None, *sorted({lg for a in self.arms.values() for lg in a.by_lang})]:
                tag = lang or "全部"
                lines.append(f"  {tag:<6} Recall@10 {self.delta(lang=lang, pair=pair):+.3f}")

        lines.extend(self._subset_note())
        lines.extend(self._ceiling_note())
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

        worst = min((a.judged_at_10 for a in self.arms.values()), default=1.0)
        if worst < 0.9:
            lines.append(
                f"\n⚠ 标注覆盖不足：最差的臂前十里只有 {worst:.0%} 命中被 gold 判定过。"
                "\n  未判定的命中一律按不相关计入分母，上表各项因此是**下界**而非测量值，"
                "\n  也不能与标注期语料上的历史数字直接相比。要出可比的数，先把 gold 扩到当前语料。"
            )
        return "\n".join(lines)

    def _significance_block(self) -> list[str]:
        """主对比的配对显著性。主指标是 nDCG@10 而不是 Recall@10 ——
        后者在这份 gold 上有 0.800 的天花板（判定粒度是章节，|relevant| 常大于 K），
        nDCG 的理想序已按 K 截断，不受影响，是这里唯一读得干净的指标。
        """
        if self.baseline not in self.arms or self.target not in self.arms:
            return []
        rows = [f"\n配对显著性（{self.target} − {self.baseline}，10k 次重采样，种子固定）："]
        if any(ONTOLOGY_PROBES[0] in a.by_probe for a in self.arms.values()):
            rows.append("  本体敏感探针（bridge_zh + alias，主 KPI）：")
            for metric in ("ndcg_at_10", "recall_at_10", "precision_at_5"):
                sig = self.significance(metric, probes=ONTOLOGY_PROBES)
                rows.append(f"    {metric:<14} {sig.render()}")
        for metric in ("ndcg_at_10", "recall_at_10", "precision_at_5"):
            sig = self.significance(metric)
            rows.append(f"  全量 {metric:<11} {sig.render()}")
        # 图像意图那 12 条上，本体臂与无本体臂逐位相同（概念挂不到图切片），
        # 它们只是往总平均里灌了 12 个恒等于零的差值，把区间往零压。
        # 检索侧改造该在哪批 query 上读，这一段就是答案。
        if "TEXT" in self.arms[self.target].by_intent:
            rows.append("  仅文本意图（图像意图上两臂逐位相同，只会把区间压向零）：")
            for metric in ("ndcg_at_10", "recall_at_10", "precision_at_5"):
                sig = self.significance(metric, intent="TEXT")
                rows.append(f"    {metric:<14} {sig.render()}")
        rows.append(
            "  n.s. = CI 跨零或 p ≥ 0.05。此规模下不显著的差值不得写成结论，"
            "\n  无论符号是正是负 —— 这条对本体臂赢和输时同样适用。"
        )
        rows.extend(self._rerank_attribution())
        return rows

    def _rerank_attribution(self) -> list[str]:
        """把"+精排"这一栏的涨幅拆成两笔：精排的、本体的。

        只报 `ontology_hybrid_rerank − bm25_only` 会把两个改动的收益记在一起，
        读者无法判断本体那部分是不是可以直接砍掉。这里的减法是：
        `bm25_rerank − bm25_only` 是精排单独的贡献，
        `ontology_hybrid_rerank − bm25_rerank` 是本体在已经有精排之后**还多给的**那部分 ——
        后者才是"本体值不值得留着"的答案，也正是敏感探针 KPI 要的那种可归因证据。
        """
        pairs = [
            ("bm25_rerank", "bm25_only", "精排单独的贡献"),
            ("ontology_hybrid_rerank", "bm25_rerank", "本体在精排之上多给的"),
            ("ontology_hybrid_rerank", "bm25_only", "两者合计"),
        ]
        if any(hi not in self.arms or lo not in self.arms for hi, lo, _ in pairs):
            return []
        rows = ["\n精排归因（nDCG@10，仅文本意图 n=25）："]
        for hi, lo, why in pairs:
            sig = self.significance("ndcg_at_10", target=hi, baseline=lo, intent="TEXT")
            rows.append(f"  {why:<16} {sig.render()}")
        return rows

    def _pool_note(self) -> list[str]:
        """候选池召回：精排能摸到的天花板。相关项没进池子，交叉编码器再准也够不着。"""
        pooled = {a.label: (a.recall_at_pool, a.pool_k) for a in self.arms.values() if a.pool_k}
        if not pooled:
            return []
        return [
            "\n候选池召回（重排前，精排的上限）：",
            *(f"  {label:<24} Recall@{k} {v:.3f}" for label, (v, k) in sorted(pooled.items())),
        ]

    def _ceiling_note(self) -> list[str]:
        """Recall@10 够不到 1.0 时，先说清楚有多少是格子不够放。

        judged@10 说的是"标注覆盖不够"，这一行说的是"相关集比 K 大"——
        两者都会把 Recall 往下压，成因和处置却完全相反：
        前者要补标注，后者只能换指标（nDCG@10 的理想序已按 K 截断，不受影响）。
        混在一起看，会把一份标注齐全的报表继续当成"标注没做完"。
        """
        ceilings = {a.label: a.recall_ceiling for a in self.arms.values()}
        if not ceilings or min(ceilings.values()) > 0.999:
            return []
        rows = [
            "\nRecall@10 上限（判定粒度=章节，相关集常大于 K，完美检索也到不了 1.0）：",
            *(f"  {label:<24} {v:.3f}" for label, v in sorted(ceilings.items())),
            "  ⚠ 上表 Recall@10 应按此上限读。跨臂比较仍然成立（同一份 gold、同一个上限），"
            "\n    但离 1.0 的那段距离不能直接当成漏检率。nDCG@10 的理想序已按 K 截断，无此问题。",
        ]
        return rows

    def _subset_note(self) -> list[str]:
        """跑了不同 query 子集的臂必须点名，否则同一张表里的两行不可比。

        视觉臂只在图像意图的 query 上评分（那是它唯一回答得了的问题），
        于是它的 Recall@10 与其余臂算的根本不是同一个平均。
        少了这行提示，读表的人会直接把两个数放在一起比。
        """
        counts = {a.label: a.query_count for a in self.arms.values() if a.query_count}
        if len(set(counts.values())) <= 1:
            return []
        full = max(counts.values())
        odd = {label: n for label, n in counts.items() if n != full}
        return [
            f"\n⚠ 以下臂跑的是 query 子集（其余臂 n={full}），与上表其余行不可横向比较：",
            *(f"  {label:<24} n={n}" for label, n in sorted(odd.items())),
        ]

    def _block(self, key: str | None, title: str, *, axis: str = "by_lang") -> str:
        cols = f"{'Recall@10':>11}{'P@5':>9}{'nDCG@10':>10}{'MRR':>8}{'MAP':>8}{'P50ms':>8}{'n':>5}"
        rows = [f"【{title}】", _pad("臂", 20) + cols, "-" * (20 + len(cols))]
        for arm in self.arms.values():
            r = arm if key is None else getattr(arm, axis).get(key)
            if r is None:
                continue
            rows.append(
                _pad(arm.label, 20)
                + f"{r.recall_at_10:>11.3f}{r.precision_at_5:>9.3f}{r.ndcg_at_10:>10.3f}"
                + f"{r.mrr:>8.3f}{r.map_score:>8.3f}{r.latency_p50_ms:>8.1f}{r.query_count:>5d}"
            )
        return "\n".join(rows)

    def _probes_block(self, probes: tuple[str, ...], title: str) -> str:
        """多探针并集的微平均表。与单探针 `_block(..., axis=by_probe)` 不同。"""
        cols = f"{'Recall@10':>11}{'P@5':>9}{'nDCG@10':>10}{'MRR':>8}{'MAP':>8}{'P50ms':>8}{'n':>5}"
        rows = [f"【{title}】", _pad("臂", 20) + cols, "-" * (20 + len(cols))]
        for arm_name, arm in self.arms.items():
            r = self._result(arm_name, None, probes=probes)
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


def _chunk_key_index(kb: KnowledgeBase) -> dict[str, list[str]]:
    """`doc_id#section` → 该节的**全部** chunk_id。gold 用稳定键，运行时用哈希键。

    值是列表而非单个 id：一节正文通常被切成多片，早先写成 dict 推导时
    同一节的后一片直接覆盖前一片 —— 588 个切片只剩 132 个键可寻址，
    另外 456 片对 gold 完全不可见。标注写得再准也命中不了，
    而失败形态是"召回莫名其妙地低"，查的人会一路查到检索器上去。

    随之确定的是 gold 的判定粒度：**一节内的全部切片同 grade**。
    人工审校面对的是章节，不是内容哈希；让标注去追切片边界，
    等于每改一次切片参数就要重标一遍。
    """
    index: dict[str, list[str]] = {}
    for c in kb.chunks:
        index.setdefault(f"{c.doc_id}#{c.section}", []).append(c.chunk_id)
    return index


def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[idx]


class _Case(NamedTuple):
    qid: str
    text: str
    lang: str
    rel: dict[str, int]
    # 该 query 问的是哪个模态。只有专用通道的臂看这一项，其余臂一律全跑。
    modality_intent: str | None = None
    # 机制探针：bridge_zh / alias / hierarchy / control / image / license。
    probe: str = "control"


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
    intent: str
    fidelity: float = 1.0
    judged: float = 1.0
    ceiling: float = 1.0
    # 候选池召回。未测（臂没设 candidate_k）时为 None，与"测了但是 0.0"要分开 ——
    # 后者是结果，前者是没问过这个问题。
    pool_recall: float | None = None
    probe: str = "control"


def _aggregate(arm: str, label: str, scores: list[_QueryScore], *, pool_k: int = 0) -> ArmResult:
    n = len(scores) or 1
    pooled = [s.pool_recall for s in scores if s.pool_recall is not None]
    return ArmResult(
        judged_at_10=sum(s.judged for s in scores) / n,
        recall_ceiling=sum(s.ceiling for s in scores) / n,
        arm=arm,
        label=label,
        recall_at_10=sum(s.recall for s in scores) / n,
        precision_at_5=sum(s.precision for s in scores) / n,
        ndcg_at_10=sum(s.ndcg for s in scores) / n,
        mrr=sum(s.rr for s in scores) / n,
        map_score=sum(s.ap for s in scores) / n,
        citation_fidelity=sum(s.fidelity for s in scores) / n,
        recall_at_pool=sum(pooled) / len(pooled) if pooled else 0.0,
        pool_k=pool_k if pooled else 0,
        latency_p50_ms=_percentile([s.elapsed_ms for s in scores], 0.50),
        latency_p95_ms=_percentile([s.elapsed_ms for s in scores], 0.95),
        query_count=len(scores),
        per_query={
            s.qid: {
                "recall": s.recall,
                "precision": s.precision,
                "ndcg": s.ndcg,
                "rr": s.rr,
                "ap": s.ap,
                **({} if s.pool_recall is None else {"pool_recall": s.pool_recall}),
            }
            for s in scores
        },
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
    reranker: Any = None,
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
    dangling: list[str] = []
    for q in gold["queries"]:
        need = q.get("requires_entitlement")
        if need and need not in entitlements:
            # 无凭据时该 query 的正解不可见，计入会把"合规过滤"错算成"召回差"。
            continue
        labels = q.get("relevant") or {}
        dangling.extend(f"{q['id']}:{k}" for k in labels if k not in index)
        rel = {cid: v for k, v in labels.items() if k in index for cid in index[k]}
        if rel:
            cases.append(
                _Case(
                    q["id"],
                    q["text"],
                    q.get("lang", "und"),
                    rel,
                    q.get("modality_intent"),
                    q.get("probe")
                    or ("image" if q.get("modality_intent") == "IMAGE" else "control"),
                )
            )

    if dangling:
        # 标注指向了不存在的切片，多半是切片键变了。静悄悄丢掉的话，
        # 召回会莫名其妙地降，而所有人都会去查检索器 —— 查错了地方。
        raise ValueError(
            f"gold 有 {len(dangling)} 条标注对不上任何切片，评测拒绝在残缺标注上出数：\n  "
            + "\n  ".join(sorted(dangling)[:10])
        )

    # gold 只在这些文档上做过判断。索引里其余文档的命中都是"没人看过"，
    # 按不相关计入分母只会低估召回。
    judged_ids = {cid for case in cases for cid in case.rel}
    judged_docs = {c.doc_id for c in kb.chunks if c.chunk_id in judged_ids}
    doc_of = {c.chunk_id: c.doc_id for c in kb.chunks}

    results: dict[str, ArmResult] = {}
    unavailable: dict[str, str] = {}

    for arm, cfg in (arms or ARMS).items():
        searcher = searchers.get(cfg.get("backend", "local"))
        if searcher is None:
            unavailable[arm] = f"{cfg.get('backend')} 后端未提供"
            continue

        # 精排臂必须拿到真模型。回落到 NullReranker 会让报表上写着"+精排"
        # 而实际是原序返回 —— 与 Milvus 臂不得回落到本地后端是同一条纪律。
        if cfg.get("rerank") and getattr(reranker, "name", "null") == "null":
            unavailable[arm] = "未提供 reranker（--reranker bge-reranker-v2-m3）"
            continue

        # 专用通道的臂只在对应意图的 query 上评分。把"只看图"的臂放到文本 query 上
        # 只会得到一串 0.000，那不是测量结果，是问错了问题。
        intent = cfg.get("modality_intent")
        selected = [c for c in cases if not intent or c.modality_intent == intent]
        if not selected:
            unavailable[arm] = f"gold 里没有 modality_intent={intent} 的 query"
            continue

        # 候选池深度。不设时等于 top_k —— 也就是今天的行为，一个字节都不变。
        # 设了就意味着"融合看得更深"，那本身是一次检索改动而不是评测口径调整，
        # 所以它属于臂配置，会单独占一行消融，不会悄悄改掉既有各臂的数字。
        candidate_k = cfg.get("candidate_k")

        scores: list[_QueryScore] = []
        for qid, text, lang, rel, q_intent, probe in selected:
            started = time.perf_counter()
            hits, _ = searcher.search(
                text,
                ctx=ctx,
                top_k=top_k,
                entitlements=entitlements,
                expand=cfg["expand"],
                channels=cfg["channels"],
                vector_fields=tuple(cfg.get("vector_fields", ())),
                modalities=tuple(cfg.get("modalities", ())),
                candidate_k=candidate_k,
                reranker=reranker if cfg.get("rerank") else None,
                rewrite=cfg.get("rewrite"),
            )
            elapsed = (time.perf_counter() - started) * 1000
            recall, precision, ndcg, rr, ap = _score_one(
                [h.chunk_id for h in hits], rel, top_k=top_k
            )
            fidelity = _citation_fidelity(kb, hits)
            top = [h.chunk_id for h in hits][:top_k]
            judged = (
                sum(1 for cid in top if doc_of.get(cid) in judged_docs) / len(top) if top else 1.0
            )
            pool_recall = None
            if candidate_k:
                # 重排前的池子。带 rerank 的臂要额外跑一次不重排的检索才能拿到它 ——
                # 多花一次前向，换来的是"精排的上限在哪、它离上限还差多少"这两个数。
                pool = searcher.search(
                    text,
                    ctx=ctx,
                    top_k=candidate_k,
                    entitlements=entitlements,
                    expand=cfg["expand"],
                    channels=cfg["channels"],
                    vector_fields=tuple(cfg.get("vector_fields", ())),
                    modalities=tuple(cfg.get("modalities", ())),
                    candidate_k=candidate_k,
                    rewrite=cfg.get("rewrite"),
                )[0]
                pool_recall = len([h for h in pool if h.chunk_id in rel]) / len(rel)
            scores.append(
                _QueryScore(
                    qid,
                    lang,
                    recall,
                    precision,
                    ndcg,
                    rr,
                    ap,
                    elapsed,
                    q_intent or "TEXT",
                    fidelity,
                    judged,
                    min(top_k, len(rel)) / len(rel),
                    pool_recall,
                    probe,
                )
            )

        pool_k = candidate_k or 0
        result = _aggregate(arm, cfg["label"], scores, pool_k=pool_k)
        for lang in sorted({s.lang for s in scores}):
            subset = [s for s in scores if s.lang == lang]
            result.by_lang[lang] = _aggregate(arm, cfg["label"], subset, pool_k=pool_k)
        # 只有真的跑了两种意图才拆 —— 视觉臂本来就只跑 IMAGE，
        # 给它拆出一个和总平均逐位相同的子表只是噪声。
        intents = sorted({s.intent for s in scores})
        if len(intents) > 1:
            for intent_key in intents:
                subset = [s for s in scores if s.intent == intent_key]
                result.by_intent[intent_key] = _aggregate(arm, cfg["label"], subset, pool_k=pool_k)
        for probe_key in sorted({s.probe for s in scores}):
            subset = [s for s in scores if s.probe == probe_key]
            result.by_probe[probe_key] = _aggregate(arm, cfg["label"], subset, pool_k=pool_k)
        results[arm] = result

    return RetrievalEval(
        arms=results,
        unavailable=unavailable,
        embedder=embedder,
        reranker=getattr(reranker, "name", ""),
    )
