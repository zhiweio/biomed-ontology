"""Docling Main Path：PDF + Office 结构化解析。

本地 import docling（MIT）。映射 Docling 文档树 → LayoutBlock；
Office 的 locator：`page` = 幻灯片序 / 工作表序（1-based），细节进 backend_meta。

PDF 像素由下游 ``render_regions`` 按 bbox 渲染（不开 ``generate_picture_images``）。
Office 嵌入图/chart 在此落盘为 ``images/docling_*.png``，供视觉融合。
"""

from __future__ import annotations

import logging
import re
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

# Docling 在未生成 picture 位图时，export_to_markdown 会写入该 HTML 注释。
_DOCLING_IMAGE_PLACEHOLDER = re.compile(
    r"<!--\s*[^>]*?(?:Image not available|generate_picture_images)[^>]*?-->",
    re.IGNORECASE | re.DOTALL,
)


class _DropTorchDtypeDeprecation(logging.Filter):
    """Docling 仍向 transformers 传 `torch_dtype=`；上游 BC 噪声，与解析结果无关。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "`torch_dtype` is deprecated" not in record.getMessage()


class DoclingBackend:
    name = "docling"

    def __init__(
        self,
        *,
        max_pages: int = 400,
        max_bytes: int = 64 * 1024 * 1024,
        render_chart_images: bool | None = None,
    ) -> None:
        self.max_pages = max_pages
        self.max_bytes = max_bytes
        if render_chart_images is None:
            from biomed_ontology.config import settings

            render_chart_images = settings.docling_render_chart_images
        self.render_chart_images = bool(render_chart_images)

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
                result = _document_converter(
                    render_chart_images=self.render_chart_images
                ).convert(
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


def _document_converter(*, render_chart_images: bool = True) -> Any:
    """关闭 layout 的 torch.compile；Office 可选渲染原生 chart 为位图。

    PDF 保持 ``generate_picture_images=False``：像素由 ``render_regions`` 统一负责。
    """
    from docling.datamodel.backend_options import (
        MsExcelBackendOptions,
        MsPowerpointBackendOptions,
        MsWordBackendOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.object_detection_engine_options import (
        TransformersObjectDetectionEngineOptions,
    )
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import (
        DocumentConverter,
        ExcelFormatOption,
        ImageFormatOption,
        PdfFormatOption,
        PowerpointFormatOption,
        WordFormatOption,
    )

    layout_options = LayoutObjectDetectionOptions.from_preset("layout_heron_default")
    layout_options.engine_options = TransformersObjectDetectionEngineOptions(
        compile_model=False,
    )
    pipeline_options = PdfPipelineOptions(layout_options=layout_options)
    office_kw = {"render_chart_images": render_chart_images}
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            InputFormat.DOCX: WordFormatOption(
                backend_options=MsWordBackendOptions(**office_kw)
            ),
            InputFormat.PPTX: PowerpointFormatOption(
                backend_options=MsPowerpointBackendOptions(**office_kw)
            ),
            InputFormat.XLSX: ExcelFormatOption(
                backend_options=MsExcelBackendOptions(**office_kw)
            ),
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
    image_idx = 0
    iterate = getattr(document, "iterate_items", None)
    if iterate is None:
        raise RuntimeError("Docling document 缺少 iterate_items")

    for item, level in iterate():
        label = str(getattr(getattr(item, "label", None), "name", getattr(item, "label", "")) or "")
        label_key = label.casefold()
        kind = _LABEL_KIND.get(label_key, "text")
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
            text = _item_text(item, document)
            heading_level = max(1, min(int(level or 1), 6))
            if text and not text.lstrip().startswith("#"):
                text = "#" * heading_level + " " + text
        elif kind == "table":
            text = _item_text(item, document) or _table_markdown(item, document)
            if not text:
                continue
            rel = f"tables/docling_{table_idx:04d}.md"
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            asset = rel
            table_idx += 1
            degraded.add("table_structure")
        elif kind == "image":
            text = _picture_caption(item, document)
            asset = _export_picture(item, document, out_dir, image_idx)
            if asset:
                image_idx += 1
        else:
            text = _item_text(item, document)
            # Docling 常把图注标成独立 CAPTION，picture.captions 却为空 ——
            # 挂到同页紧前的空 caption 图块上，供 IMAGE Evidence 文本面检索。
            if label_key == "caption" and text:
                _attach_caption_to_prior_image(blocks, page=page or 1, caption=text)
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


def _attach_caption_to_prior_image(
    blocks: list[LayoutBlock], *, page: int, caption: str
) -> None:
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b.page != page:
            break
        if b.kind != "image":
            continue
        if b.text.strip():
            return
        blocks[i] = LayoutBlock(
            kind=b.kind,
            text=caption,
            page=b.page,
            bbox=b.bbox,
            level=b.level,
            asset_path=b.asset_path,
            backend_meta=b.backend_meta,
        )
        return



def _picture_caption(item: Any, document: Any) -> str:
    """image 块只保留真实 caption，剥离 Docling 的 Image-not-available 占位注释。"""
    for attr in ("text", "orig"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.strip():
            return _strip_docling_image_placeholder(val)
    return _strip_docling_image_placeholder(_export_markdown(item, document))


def _strip_docling_image_placeholder(text: str) -> str:
    cleaned = _DOCLING_IMAGE_PLACEHOLDER.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _export_picture(
    item: Any, document: Any, out_dir: Path, idx: int
) -> str | None:
    """Office 嵌入图 / 已挂 ImageRef 的 picture → PNG；失败返回 None。"""
    getter = getattr(item, "get_image", None)
    pil = None
    if callable(getter):
        try:
            pil = getter(document)
        except Exception:
            pil = None
    if pil is None:
        image_ref = getattr(item, "image", None)
        pil = getattr(image_ref, "pil_image", None) if image_ref is not None else None
    if pil is None:
        return None
    rel = f"images/docling_{idx:04d}.png"
    target = out_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        pil.save(target, format="PNG")
    except Exception:
        return None
    return rel if target.is_file() else None


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
