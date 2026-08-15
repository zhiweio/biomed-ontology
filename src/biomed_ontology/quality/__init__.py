"""质量体系与发版守门（L7）。

三层校验各管一件事，不可互相替代：
  - 结构校验（SHACL/JSON Schema）保证"格式对"；
  - 一致性校验保证"内部不打架"（环、孤儿、跨源冲突）；
  - 抽样人工核验保证"内容对" —— 前两层永远发现不了一条格式完美的错误事实。

守门规则刻意做成硬阻断而非告警：质量告警在任何组织里都会被"下次再改"消化掉，
只有阻断发版才能让质量问题真正进入排期。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.observability import MetricPoint
from biomed_ontology.pipeline import KnowledgeBase

__all__ = [
    "GateDecision",
    "QualityGate",
    "QualityReport",
    "SamplingPlan",
    "check_consistency",
    "stratified_sample",
]

# 计划中的守门线：任一核心类型准确率 < 90%，或较上版下降 > 2 个点。
ACCURACY_FLOOR = 0.90
REGRESSION_TOLERANCE = 0.02


@dataclass
class Violation:
    rule: str
    severity: str
    subject: str
    detail: str


@dataclass
class QualityReport:
    release_id: str
    metrics: list[MetricPoint] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "ERROR"]

    def metric(self, name: str, dimension: str | None = None) -> float | None:
        for m in self.metrics:
            if m.metric_name == name and m.metric_dimension == dimension:
                return m.metric_value
        return None

    def as_table(self) -> list[tuple[str, str, str]]:
        return [
            (
                m.metric_name + (f"[{m.metric_dimension}]" if m.metric_dimension else ""),
                f"{m.metric_value:.4f}",
                "PASS" if m.passed is not False else "FAIL",
            )
            for m in self.metrics
        ]


# ---------------------------------------------------------------- 一致性


def check_consistency(kb: KnowledgeBase) -> list[Violation]:
    """图侧一致性。这些错误 SHACL 查不出来，因为它们不是形状问题而是图结构问题。"""
    out: list[Violation] = []
    by_id = {c.concept_id: c for c in kb.concepts}

    # 1. 悬空父节点
    for c in kb.concepts:
        for p in c.parents:
            if p not in by_id:
                out.append(
                    Violation("dangling_parent", "ERROR", c.concept_id, f"父节点 {p} 不存在")
                )

    # 2. 层级环。环会让 expand 无限递归，也会让"上位概念"这个说法失去意义。
    for cid in by_id:
        seen: set[str] = set()
        cur = [cid]
        while cur:
            nxt = []
            for x in cur:
                if x in seen:
                    continue
                seen.add(x)
                nxt.extend(by_id[x].parents if x in by_id else [])
            if cid in nxt:
                out.append(Violation("hierarchy_cycle", "ERROR", cid, "层级中存在环"))
                break
            cur = nxt

    # 3. 归属可疑的事实：证据文档的 tier 高于事实自身声明的 tier。
    for f in kb.facts:
        worst = max(
            (kb.doc_tier(e.doc_id) for e in f.evidence),
            key=lambda t: int(t.value.rsplit("_", 1)[-1]),
            default=LicenseTierEnum.TIER_0,
        )
        if int(worst.value.rsplit("_", 1)[-1]) > int(f.license_tier.value.rsplit("_", 1)[-1]):
            out.append(
                Violation(
                    "license_downgrade",
                    "ERROR",
                    f.fact_id,
                    f"事实标为 {f.license_tier.value}，但证据来自 {worst.value}",
                )
            )

    # 4. 无证据事实。没有出处的事实在药物研发里不可用 —— 研究员无法复核就不会采信。
    for f in kb.facts:
        if not f.evidence:
            out.append(Violation("fact_without_evidence", "ERROR", f.fact_id, "缺少证据"))

    # 5. 同一 (subject, predicate, metric) 下数值互斥
    for key, vals in _numeric_groups(kb).items():
        distinct = {v for _, v in vals}
        if len(distinct) > 1:
            out.append(
                Violation(
                    "conflicting_values",
                    "WARN",
                    "|".join(key),
                    f"同一指标出现互斥数值：{sorted(distinct)}",
                )
            )

    # 6. 孤立概念：既无父也无别名，通常是录入半途而废的产物
    alias_owners = {s.concept_id for s in kb.synonyms}
    for c in kb.concepts:
        if not c.parents and c.concept_id not in alias_owners:
            out.append(Violation("orphan_concept", "WARN", c.concept_id, "无父节点且无别名"))

    # 7. 层级边指向字面量。这类错误不会报错，只会让 SPARQL 层级遍历
    # 在某个节点静默断掉，因此必须在守门处显式拦一道。
    rows = kb.graph.query(
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
        "SELECT ?c ?p WHERE { GRAPH ?g { ?c skos:broader ?p } FILTER(isLiteral(?p)) }",
        unrestricted=True,
    )
    for r in rows:
        out.append(
            Violation(
                "literal_hierarchy_edge", "ERROR", r["c"], f"skos:broader 指向字面量 {r['p']}"
            )
        )
    return out


def _numeric_groups(kb: KnowledgeBase) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    groups: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for f in kb.facts:
        if f.object_value is None:
            continue
        metric = next((q.split("=", 1)[1] for q in f.qualifiers if q.startswith("metric=")), "")
        population = next((q for q in f.qualifiers if q.startswith("population=")), "")
        key = (f.subject_id, metric, population)
        groups.setdefault(key, []).append((f.fact_id, f.object_value))
    return {k: v for k, v in groups.items() if k[1]}


# ---------------------------------------------------------------- 抽样


@dataclass
class SamplingPlan:
    """分层抽样。按模态与实体类型分层，而不是全局随机。

    全局随机抽样会被占比最大的那一层主导：文本事实数量压过图像事实一个数量级，
    随机抽 30 条几乎抽不到图像，而图像恰恰是准确率最低、最需要盯的通道。
    """

    strata: dict[str, list[str]]
    per_stratum: int
    seed: int = 20260801

    def draw(self) -> dict[str, list[str]]:
        rng = random.Random(self.seed)
        return {
            name: rng.sample(items, min(self.per_stratum, len(items)))
            for name, items in sorted(self.strata.items())
            if items
        }


def stratified_sample(
    kb: KnowledgeBase, *, per_stratum: int = 5, seed: int = 20260801
) -> SamplingPlan:
    strata: dict[str, list[str]] = {}
    for f in kb.facts:
        strata.setdefault(f"modality:{f.modality.value}", []).append(f.fact_id)
    for c in kb.concepts:
        strata.setdefault(f"entity:{c.entity_type.value}", []).append(c.concept_id)
    return SamplingPlan(strata=strata, per_stratum=per_stratum, seed=seed)


# ---------------------------------------------------------------- 守门


@dataclass
class GateDecision:
    passed: bool
    blocking: list[str]
    report: QualityReport

    def explain(self) -> str:
        if self.passed:
            return f"发版守门通过（release {self.report.release_id}）"
        return "发版阻断：\n" + "\n".join(f"  - {b}" for b in self.blocking)


class QualityGate:
    def __init__(
        self,
        *,
        accuracy_floor: float = ACCURACY_FLOOR,
        regression_tolerance: float = REGRESSION_TOLERANCE,
    ) -> None:
        self.accuracy_floor = accuracy_floor
        self.regression_tolerance = regression_tolerance

    def evaluate(
        self,
        kb: KnowledgeBase,
        *,
        manual_accuracy: dict[str, float] | None = None,
        previous: dict[str, float] | None = None,
        shapes_path: Path | None = None,
    ) -> GateDecision:
        report = QualityReport(release_id=kb.release_id)
        report.violations.extend(check_consistency(kb))

        if shapes_path and shapes_path.exists():
            shacl = kb.graph.validate_shacl(shapes_path)
            report.metrics.append(
                MetricPoint(
                    metric_name="shacl_conforms",
                    metric_value=1.0 if shacl.conforms else 0.0,
                    ontology_release_id=kb.release_id,
                    passed=shacl.conforms,
                )
            )
            for v in shacl.violations[:20]:
                report.violations.append(Violation("shacl", "ERROR", "graph", v))

        report.metrics.extend(self._structural_metrics(kb))

        blocking: list[str] = []
        for v in report.errors():
            blocking.append(f"[{v.rule}] {v.subject}: {v.detail}")

        prev = previous or {}
        for entity_type, acc in sorted((manual_accuracy or {}).items()):
            passed = acc >= self.accuracy_floor
            regressed = (
                entity_type in prev and (prev[entity_type] - acc) > self.regression_tolerance
            )
            report.metrics.append(
                MetricPoint(
                    metric_name="manual_accuracy",
                    metric_value=acc,
                    ontology_release_id=kb.release_id,
                    metric_dimension=entity_type,
                    threshold=self.accuracy_floor,
                    passed=passed and not regressed,
                )
            )
            if not passed:
                blocking.append(
                    f"[accuracy_floor] {entity_type} 准确率 {acc:.3f} < {self.accuracy_floor:.2f}"
                )
            if regressed:
                blocking.append(
                    f"[regression] {entity_type} 较上版下降 {prev[entity_type] - acc:.3f}"
                    f" > {self.regression_tolerance:.2f}"
                )

        for m in report.metrics:
            kb.hub.record_metric(m)
        return GateDecision(passed=not blocking, blocking=blocking, report=report)

    def evaluate_claims(
        self,
        claims: list[Any],
        *,
        release_id: str = "extracted",
    ) -> GateDecision:
        """入图前的 claim 级守门：无证据阻断；互斥数值告警。不自动 validated。"""
        report = QualityReport(release_id=release_id)
        blocking: list[str] = []
        groups: dict[tuple[str, str, str], set[str]] = {}
        for raw in claims:
            claim = raw if isinstance(raw, dict) else getattr(raw, "__dict__", {})
            cid = str(claim.get("claim_id") or "")
            evidence = claim.get("evidence_ids") or claim.get("evidence") or []
            if not evidence:
                report.violations.append(
                    Violation("fact_without_evidence", "ERROR", cid or "claim", "缺少证据")
                )
                blocking.append(f"[fact_without_evidence] {cid or 'claim'}: 缺少证据")
            subject = str(claim.get("subject_id") or "")
            pred = str(claim.get("predicate") or "")
            metric = ""
            for q in claim.get("qualifiers") or []:
                if str(q).startswith("metric="):
                    metric = str(q).split("=", 1)[1]
            value = claim.get("object_value")
            if subject and pred and value:
                groups.setdefault((subject, pred, metric), set()).add(str(value))
        for key, vals in groups.items():
            if len(vals) > 1:
                label = "|".join(key)
                report.violations.append(
                    Violation(
                        "conflicting_values",
                        "WARN",
                        label,
                        f"同一指标出现互斥数值：{sorted(vals)}",
                    )
                )
        return GateDecision(passed=not blocking, blocking=blocking, report=report)

    def _structural_metrics(self, kb: KnowledgeBase) -> list[MetricPoint]:
        n_c = max(1, len(kb.concepts))
        with_zh = sum(1 for c in kb.concepts if c.preferred_label_zh)
        with_def = sum(1 for c in kb.concepts if c.definition)
        aliases_per = len(kb.synonyms) / n_c
        grounded = sum(1 for ch in kb.chunks if ch.concept_ids)
        return [
            MetricPoint("concept_count", float(len(kb.concepts)), kb.release_id),
            MetricPoint("alias_per_concept", round(aliases_per, 3), kb.release_id),
            MetricPoint("zh_label_coverage", round(with_zh / n_c, 4), kb.release_id, passed=True),
            MetricPoint("definition_coverage", round(with_def / n_c, 4), kb.release_id),
            MetricPoint(
                "chunk_grounding_rate",
                round(grounded / max(1, len(kb.chunks)), 4),
                kb.release_id,
                sample_size=len(kb.chunks),
            ),
            MetricPoint(
                "fact_evidence_rate",
                round(
                    sum(1 for f in kb.facts if f.evidence) / max(1, len(kb.facts)),
                    4,
                ),
                kb.release_id,
                sample_size=len(kb.facts),
            ),
            MetricPoint("label_coverage", float(kb.stats()["label_coverage"]), kb.release_id),
        ]
