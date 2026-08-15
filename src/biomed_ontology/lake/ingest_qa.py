"""入湖质检（IngestQA）。与发版 QualityGate 分开：只管文档能否入库，不管 KB 发版。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["IngestQAError", "IngestQAReport", "run_ingest_qa"]

_DEGRADED_THRESHOLD = 0.4
_KNOWN_CAPS = frozenset({"bbox", "ocr", "formula", "table_structure"})


class IngestQAError(RuntimeError):
    """入湖质检阻断。失败要大声，禁止静默入库。"""

    def __init__(self, report: IngestQAReport) -> None:
        self.report = report
        super().__init__(report.explain())


@dataclass
class IngestQAReport:
    passed: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def explain(self) -> str:
        if self.passed:
            return "IngestQA 通过"
        return "IngestQA 阻断：\n" + "\n".join(f"  - {b}" for b in self.blocking)


def run_ingest_qa(
    ctx: Any,
    *,
    degraded_threshold: float = _DEGRADED_THRESHOLD,
    require_source: bool = True,
    strict: bool = True,
) -> IngestQAReport:
    """空树、降级超阈、来源未登记、doc_id 非法 → 阻断。"""
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    doc_id = str(getattr(ctx, "doc_id", "") or "").strip()
    checks["doc_id"] = doc_id
    if not doc_id:
        blocking.append("doc_id 为空，无法幂等入库")
    elif any(ch.isspace() for ch in doc_id):
        blocking.append(f"doc_id 含空白，无法作为幂等键：{doc_id!r}")

    chunks = list(getattr(ctx, "chunks", None) or [])
    nonempty = [c for c in chunks if str(getattr(c, "text", "") or "").strip()]
    checks["chunk_count"] = len(chunks)
    checks["nonempty_chunks"] = len(nonempty)
    if not nonempty:
        blocking.append("语义树为空（无非空 chunk），拒绝入库")

    degraded = [str(x) for x in (getattr(ctx, "parse_degraded", None) or [])]
    checks["degraded"] = degraded
    if _KNOWN_CAPS:
        ratio = len(set(degraded) & _KNOWN_CAPS) / len(_KNOWN_CAPS)
        checks["degraded_ratio"] = round(ratio, 4)
        if ratio > degraded_threshold:
            blocking.append(
                f"版面降级比例 {ratio:.2f} 超过阈值 {degraded_threshold:.2f}：{degraded}"
            )

    source_id = str(getattr(ctx, "source_id", "") or "").strip()
    checks["source_id"] = source_id
    if require_source:
        if not source_id:
            blocking.append("source_id 未登记")
        else:
            try:
                from biomed_ontology.registry import load_registry

                registry = load_registry()
                if source_id not in registry:
                    blocking.append(f"source_id 未在 registry 登记：{source_id!r}")
            except Exception as exc:
                warnings.append(f"registry 不可读：{exc}")

    document = getattr(ctx, "document", None)
    if document is not None:
        checks["license_tier"] = str(getattr(document, "license_tier", "") or "")
        if not getattr(document, "license_tier", None):
            blocking.append("文档 license_tier 未登记")

    report = IngestQAReport(
        passed=not blocking,
        blocking=blocking,
        warnings=warnings,
        checks=checks,
    )
    if strict and not report.passed:
        raise IngestQAError(report)
    return report
