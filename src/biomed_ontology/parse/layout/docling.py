"""Docling Main Path：PDF + Office 结构化解析。

本地 import docling（MIT）。映射 Docling 文档树 → LayoutBlock；
Office 的 locator：`page` = 幻灯片序 / 工作表序（1-based），细节进 backend_meta。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import MappingJustificationEnum
from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import Capability, LayoutBlock, LayoutResult

__all__ = ["DoclingBackend"]

_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".png", ".jpg", ".jpeg"}

_LABEL_KIND: dict[str, str] = {
    "title": "heading",
    "section_header": "heading",
    "text": "text",
    "paragraph": "text",
    "list_item": "text",
    "caption": "text",
    "footnote": "text",
    "table": "table",
    "picture": "image",
    "chart": "image",
    "formula": "formula",
    "code": "text",
}


class _DropTorchDtypeDeprecation(logging.Filter):
    """Docling 仍向 transformers 传 `torch_dtype=`；上游 BC 噪声，与解析结果无关。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "`torch_dtype` is deprecated" not in record.getMessage()


class DoclingBackend:
    name = "docling"

    def __init__(self, *, max_pages: int = 400, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_pages = max_pages
        self.max_bytes = max_bytes

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in _SUFFIXES

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"{path.name} 为 {size} 字节，超过上限 {self.max_bytes}")

        out_dir.mkdir(parents=True, exist_ok=True)
        locator_kind = _locator_kind(path)
        tf_log = logging.getLogger("transformers")
        drop = _DropTorchDtypeDeprecation()
        tf_log.addFilter(drop)
        try:
            with ctx.span("layout.docling", doc=path.name) as span:
                result = _document_converter().convert(
                    str(path),
                    max_num_pages=self.max_pages,
                    max_file_size=self.max_bytes,
                )
                blocks, degraded, page_count = _from_docling(
                    result.document, out_dir, locator_kind=locator_kind
                )
                span.attributes["blocks"] = len(blocks)
                span.attributes["degraded"] = sorted(degraded)
        finally:
            tf_log.removeFilter(drop)

        if degraded:
            ctx.record_decision(
                stage="parse.layout",
                justification=MappingJustificationEnum.UnspecifiedMatching,
                chosen=self.name,
                confidence=1.0,
                rule_id="layout.degraded",
                state_after=f"Docling 能力缺口：{'、'.join(sorted(degraded))}",
            )

        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=page_count,
            backend=self.name,
            degraded=tuple(sorted(degraded)),
        )


def _document_converter() -> Any:
    """关闭 layout 的 torch.compile，避免 MPS/少 SM 机上的 dynamo graph-break 噪声。"""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.object_detection_engine_options import (
        TransformersObjectDetectionEngineOptions,
    )
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    layout_options = LayoutObjectDetectionOptions.from_preset("layout_heron_default")
    layout_options.engine_options = TransformersObjectDetectionEngineOptions(
        compile_model=False,
    )
    pipeline_options = PdfPipelineOptions(layout_options=layout_options)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


def _locator_kind(path: Path) -> str:
    suf = path.suffix.casefold()
    if suf == ".pptx":
        return "slide"
    if suf == ".xlsx":
        return "sheet"
    return "page"


def _from_docling(
    document: Any, out_dir: Path, *, locator_kind: str
) -> tuple[list[LayoutBlock], set[Capability], int]:
    blocks: list[LayoutBlock] = []
    degraded: set[Capability] = set()
    max_page = 0
    table_idx = 0
    iterate = getattr(document, "iterate_items", None)
    if iterate is None:
        raise RuntimeError("Docling document 缺少 iterate_items")

    for item, level in iterate():
        label = str(getattr(getattr(item, "label", None), "name", getattr(item, "label", "")) or "")
        label_key = label.casefold()
        kind = _LABEL_KIND.get(label_key, "text")
        text = _item_text(item, document)
        page, bbox, meta = _provenance(item, locator_kind=locator_kind)
        if page:
            max_page = max(max_page, page)
        if not bbox:
            degraded.add("bbox")
        if kind == "formula":
            degraded.add("formula")
        asset: str | None = None
        heading_level = None
        if kind == "heading":
            heading_level = max(1, min(int(level or 1), 6))
            if text and not text.lstrip().startswith("#"):
                text = "#" * heading_level + " " + text
        if kind == "table":
            md = text or _table_markdown(item, document)
            if not md:
                continue
            rel = f"tables/docling_{table_idx:04d}.md"
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(md, encoding="utf-8")
            asset = rel
            text = md
            table_idx += 1
            degraded.add("table_structure")
        if not text and kind not in {"image"}:
            continue
        blocks.append(
            LayoutBlock(
                kind=kind,  # type: ignore[arg-type]
                text=text or "",
                page=page or 1,
                bbox=bbox,
                level=heading_level,
                asset_path=asset,
                backend_meta=meta,
            )
        )
    return blocks, degraded, max(max_page, 1)


def _item_text(item: Any, document: Any) -> str:
    for attr in ("text", "orig"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return _export_markdown(item, document)


def _table_markdown(item: Any, document: Any) -> str:
    return _export_markdown(item, document)


def _export_markdown(item: Any, document: Any) -> str:
    export = getattr(item, "export_to_markdown", None)
    if not callable(export):
        return ""
    try:
        return str(export(doc=document) or "").strip()
    except TypeError:
        # 假 Document / 旧签名：退回无参调用。
        try:
            return str(export() or "").strip()
        except Exception:
            return ""
    except Exception:
        return ""


def _provenance(
    item: Any, *, locator_kind: str
) -> tuple[int, tuple[float, ...], dict[str, object]]:
    provs = list(getattr(item, "prov", None) or [])
    meta: dict[str, object] = {"locator_kind": locator_kind}
    if not provs:
        return 0, (), meta
    prov = provs[0]
    page_raw = getattr(prov, "page_no", None) or getattr(prov, "page", None) or 0
    page = int(page_raw)
    bbox_obj = getattr(prov, "bbox", None)
    bbox: tuple[float, ...] = ()
    if bbox_obj is not None:
        try:
            if hasattr(bbox_obj, "as_tuple"):
                bbox = tuple(float(v) for v in bbox_obj.as_tuple())
            else:
                bbox = tuple(
                    float(getattr(bbox_obj, k))
                    for k in ("l", "t", "r", "b")
                    if hasattr(bbox_obj, k)
                )
                if len(bbox) != 4:
                    bbox = tuple(float(v) for v in bbox_obj)  # type: ignore[arg-type]
        except Exception:
            bbox = ()
    return page, bbox if len(bbox) == 4 else (), meta
