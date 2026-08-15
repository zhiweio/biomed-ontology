"""Document Router：按格式与复杂度选择版面后端，可选自动降级。

配置 `HMD_LAYOUT_BACKEND=auto` 时由此模块决策；显式后端名则跳过 probe。
`HMD_LAYOUT_FALLBACK=true` 时按链重试并写入 route_trace。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from biomed_ontology._generated.hmd_concept import MappingJustificationEnum
from biomed_ontology.config import Settings
from biomed_ontology.config import settings as default_settings
from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import Capability, LayoutResult
from biomed_ontology.parse.layout.registry import get_layout_backend

__all__ = [
    "FALLBACK_TRIGGERS",
    "RouteDecision",
    "RouteTrace",
    "UnsupportedFormat",
    "route_and_extract",
    "select_backend",
]

BackendName = Literal["pymupdf4llm", "docling", "mineru", "text"]
_KNOWN_BACKENDS: frozenset[str] = frozenset({"pymupdf4llm", "docling", "mineru", "text"})

FALLBACK_TRIGGERS: frozenset[Capability] = frozenset(
    {"ocr", "formula", "table_structure", "reading_order"}
)

_OFFICE = {".docx", ".pptx", ".xlsx"}
_LEGACY_OFFICE = {".doc", ".ppt"}
_IMAGE = {".png", ".jpg", ".jpeg"}
_PDF = {".pdf", ".xps", ".epub"}
_HTML = {".html", ".htm"}
_TEXT = {".txt", ".md"}


class UnsupportedFormat(ValueError):
    """后缀不在三路径支持矩阵内。"""


@dataclass(frozen=True)
class RouteDecision:
    backend: BackendName
    reason: str
    confidence: float
    probe: dict[str, object] = field(default_factory=dict)


@dataclass
class RouteTrace:
    requested: str
    chosen: str
    reason: str
    probe: dict[str, object] = field(default_factory=dict)
    attempts: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "chosen": self.chosen,
            "reason": self.reason,
            "probe": self.probe,
            "attempts": self.attempts,
        }


def select_backend(
    path: Path,
    *,
    config: Settings | None = None,
    forced: str | None = None,
) -> RouteDecision:
    cfg = config or default_settings
    name = forced or cfg.layout_backend
    if name and name != "auto":
        if name == "pymupdf":
            raise ValueError("版面后端 'pymupdf' 已废弃，请改用 'pymupdf4llm'（或 'auto'）")
        if name not in _KNOWN_BACKENDS:
            raise ValueError(f"未知版面后端：{name!r}")
        return RouteDecision(backend=cast(BackendName, name), reason="forced", confidence=1.0)

    suf = path.suffix.casefold()
    if suf in _OFFICE:
        return RouteDecision(backend="docling", reason="office_main", confidence=0.95)
    if suf in _LEGACY_OFFICE:
        return RouteDecision(backend="mineru", reason="legacy_office", confidence=0.75)
    if suf in _HTML:
        return RouteDecision(backend="docling", reason="html_main", confidence=0.9)
    if suf in _TEXT:
        return RouteDecision(backend="text", reason="plain_text", confidence=1.0)
    if suf in _IMAGE:
        return RouteDecision(backend="mineru", reason="image_ocr", confidence=0.8)
    if suf not in _PDF:
        raise UnsupportedFormat(f"不支持的文档格式：{suf or path.name}")

    from biomed_ontology.parse.layout._pdf_io import probe_pdf

    probe = probe_pdf(path, max_pages=cfg.parse_max_pages, max_bytes=cfg.parse_max_bytes)
    pdata = probe.as_dict()
    if not probe.text_extractable:
        return RouteDecision(
            backend="mineru", reason="low_text_extractable", confidence=0.85, probe=pdata
        )
    if (
        probe.page_count <= cfg.parse_fast_max_pages
        and probe.image_count <= cfg.parse_fast_max_images
        and probe.table_candidates <= cfg.parse_fast_max_tables
        and not probe.multi_column_hint
    ):
        return RouteDecision(
            backend="pymupdf4llm", reason="simple_pdf", confidence=0.9, probe=pdata
        )
    return RouteDecision(backend="docling", reason="structured_pdf", confidence=0.85, probe=pdata)


def route_and_extract(
    path: Path,
    out_dir: Path,
    *,
    ctx: TraceContext,
    config: Settings | None = None,
    forced: str | None = None,
) -> tuple[LayoutResult, RouteTrace]:
    cfg = config or default_settings
    decision = select_backend(path, config=cfg, forced=forced)
    chain = _fallback_chain(decision.backend, path)
    trace = RouteTrace(
        requested=forced or cfg.layout_backend,
        chosen=decision.backend,
        reason=decision.reason,
        probe=dict(decision.probe),
    )

    last_error: Exception | None = None
    merged_degraded: set[str] = set()
    for backend_name in chain:
        backend = get_layout_backend(backend_name, config=cfg)
        if not backend.supports(path):
            trace.attempts.append({"backend": backend_name, "status": "unsupported", "error": None})
            continue
        try:
            result = backend.extract(path, out_dir, ctx=ctx)
        except Exception as exc:
            last_error = exc
            trace.attempts.append(
                {"backend": backend_name, "status": "error", "error": str(exc)[:300]}
            )
            if not cfg.layout_fallback:
                raise
            continue

        merged_degraded.update(result.degraded)
        attempt: dict[str, object] = {
            "backend": backend_name,
            "status": "ok",
            "degraded": list(result.degraded),
            "blocks": len(result.blocks),
        }
        need_fallback = cfg.layout_fallback and _should_fallback(result)
        if need_fallback and backend_name != chain[-1]:
            attempt["status"] = "degraded_fallback"
            trace.attempts.append(attempt)
            continue

        trace.attempts.append(attempt)
        trace.chosen = backend_name
        if merged_degraded and set(result.degraded) != merged_degraded:
            result = LayoutResult(
                blocks=result.blocks,
                assets_dir=result.assets_dir,
                page_count=result.page_count,
                backend=result.backend,
                degraded=cast(tuple[Capability, ...], tuple(sorted(merged_degraded))),
            )
        ctx.record_decision(
            stage="parse.route",
            justification=MappingJustificationEnum.UnspecifiedMatching,
            chosen=trace.chosen,
            confidence=decision.confidence,
            rule_id=f"route.{decision.reason}",
            state_after=str(trace.as_dict()),
        )
        return result, trace

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Document Router 无法解析 {path.name}：无可用后端")


def _fallback_chain(primary: BackendName, path: Path) -> list[BackendName]:
    suf = path.suffix.casefold()
    if suf in _OFFICE:
        # XLSX 仅 Docling；DOCX/PPT 可降 MinerU
        if suf == ".xlsx":
            return ["docling"]
        chain: list[BackendName] = ["docling", "mineru"]
        if primary in chain:
            return [primary, *[b for b in chain if b != primary]]
        return chain
    if suf in _LEGACY_OFFICE:
        return ["mineru", "docling"] if primary == "mineru" else ["docling", "mineru"]
    if suf in _HTML:
        return ["docling"]
    if suf in _TEXT:
        return ["text"]
    if suf in _IMAGE:
        return ["mineru", "docling"] if primary == "mineru" else ["docling", "mineru"]
    order: list[BackendName] = ["pymupdf4llm", "docling", "mineru"]
    if primary not in order:
        return order
    return [primary, *[b for b in order if b != primary]]


def _should_fallback(result: LayoutResult) -> bool:
    if not result.blocks:
        return True
    hits = FALLBACK_TRIGGERS.intersection(result.degraded)
    return bool(hits) and len(result.blocks) < 8


def attach_route_to_parse(parse_obj: dict[str, Any], trace: RouteTrace) -> dict[str, Any]:
    out = dict(parse_obj)
    out["route"] = trace.as_dict()
    return out
