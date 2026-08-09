"""PyMuPDF4LLM Fast Path：简单 PDF 的主本地后端。

对外 `name="pymupdf4llm"`。底层仍可能 `import pymupdf`（渲染/限额），
但不得再暴露名为 `pymupdf` 的 LayoutBackend。

Fast Path 关闭 OCR（`use_ocr=False`）：扫描件应走 MinerU，并在此声明 `ocr` degraded。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import MappingJustificationEnum
from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout._pdf_io import open_pdf
from biomed_ontology.parse.layout.base import Capability, LayoutBlock, LayoutResult

__all__ = ["PyMuPDF4LLMBackend"]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_WS = re.compile(r"\s+")

_CLASS_KIND: dict[str, str] = {
    "section-header": "heading",
    "title": "heading",
    "text": "text",
    "paragraph": "text",
    "list-item": "text",
    "table": "table",
    "image": "image",
    "picture": "image",
    "formula": "formula",
    "equation": "formula",
    "caption": "text",
}


class PyMuPDF4LLMBackend:
    name = "pymupdf4llm"

    def __init__(self, *, max_pages: int = 400, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_pages = max_pages
        self.max_bytes = max_bytes

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in {".pdf", ".xps", ".epub"}

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        import pymupdf4llm

        # 限额校验（顺带确认文件可开）
        doc = open_pdf(path, max_pages=self.max_pages, max_bytes=self.max_bytes)
        page_count = int(doc.page_count)
        doc.close()

        out_dir.mkdir(parents=True, exist_ok=True)
        with ctx.span("layout.pymupdf4llm", doc=path.name) as span:
            chunks = pymupdf4llm.to_markdown(
                str(path),
                page_chunks=True,
                write_images=False,
                use_ocr=False,
                show_progress=False,
            )
            if isinstance(chunks, str):
                blocks, degraded = _from_plain_markdown(chunks), {"bbox"}
                page_count = max(page_count, 1)
            else:
                blocks, degraded = _from_page_chunks(list(chunks), out_dir)
                if chunks:
                    meta = chunks[0].get("metadata") or {}
                    page_count = int(meta.get("page_count") or page_count)

            # 扫描页：有图无字 → 声明 ocr（Fast Path 不做 OCR）
            degraded |= _scan_ocr_gaps(path, page_count)

            span.attributes["blocks"] = len(blocks)
            span.attributes["degraded"] = sorted(degraded)

        if degraded:
            ctx.record_decision(
                stage="parse.layout",
                justification=MappingJustificationEnum.UnspecifiedMatching,
                chosen=self.name,
                confidence=1.0,
                rule_id="layout.degraded",
                state_after=(
                    f"PyMuPDF4LLM 能力缺口：{'、'.join(sorted(degraded))}；"
                    "可改走 Docling/MinerU"
                ),
            )

        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=page_count,
            backend=self.name,
            degraded=tuple(sorted(degraded)),
        )


def _from_page_chunks(
    chunks: list[dict[str, Any]], out_dir: Path
) -> tuple[list[LayoutBlock], set[Capability]]:
    blocks: list[LayoutBlock] = []
    degraded: set[Capability] = set()
    table_idx = 0
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        page = int(meta.get("page_number") or meta.get("page") or 1)
        text = str(chunk.get("text") or "")
        boxes = list(chunk.get("page_boxes") or meta.get("page_boxes") or [])
        if not boxes:
            # 无 layout boxes：按 markdown 行拆，声明 bbox
            degraded.add("bbox")
            blocks.extend(_markdown_lines_to_blocks(text, page=page))
            continue
        for box in boxes:
            cls = str(box.get("class") or box.get("boxclass") or "text").casefold()
            kind = _CLASS_KIND.get(cls, "text")
            raw_bbox = box.get("bbox") or (
                box.get("x0"),
                box.get("y0"),
                box.get("x1"),
                box.get("y1"),
            )
            bbox = _bbox(raw_bbox)
            pos = box.get("pos")
            slice_text = ""
            if isinstance(pos, (list, tuple)) and len(pos) == 2 and text:
                a, b = int(pos[0]), int(pos[1])
                slice_text = text[a:b]
            fallback = str(box.get("text") or "")
            slice_text = _WS.sub(" ", slice_text).strip() or _WS.sub(" ", fallback).strip()
            if not slice_text and kind not in {"image", "table"}:
                continue
            level = None
            if kind == "heading":
                m = _HEADING.match(slice_text)
                if m:
                    level = len(m.group(1))
                    slice_text = m.group(0)
                else:
                    level = int(box.get("header_level") or 1) or 1
                    slice_text = "#" * level + " " + slice_text.lstrip("#").strip()
            asset: str | None = None
            if kind == "table":
                rel = f"tables/p{page:04d}_t{table_idx}.md"
                target = out_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(slice_text or "", encoding="utf-8")
                asset = rel
                table_idx += 1
                degraded.add("table_structure")
            if kind == "formula":
                degraded.add("formula")
            blocks.append(
                LayoutBlock(
                    kind=kind,  # type: ignore[arg-type]
                    text=slice_text,
                    page=page,
                    bbox=bbox,
                    level=level,
                    asset_path=asset,
                    backend_meta={"box_class": cls},
                )
            )
    return blocks, degraded


def _from_plain_markdown(md: str) -> list[LayoutBlock]:
    return _markdown_lines_to_blocks(md, page=1)


def _markdown_lines_to_blocks(md: str, *, page: int) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(LayoutBlock(kind="heading", text=line, page=page, level=len(m.group(1))))
        else:
            blocks.append(LayoutBlock(kind="text", text=line, page=page))
    return blocks


def _bbox(raw: Any) -> tuple[float, ...]:
    if raw is None:
        return ()
    try:
        vals = tuple(float(v) for v in raw)
    except (TypeError, ValueError):
        return ()
    if len(vals) != 4 or any(v != v for v in vals):  # NaN
        return ()
    return vals


def _scan_ocr_gaps(path: Path, page_count: int) -> set[Capability]:
    """有像素无文本的页 → ocr degraded。"""
    try:
        import pymupdf
    except ImportError:
        return set()
    degraded: set[Capability] = set()
    with pymupdf.open(path) as doc:
        for i in range(min(page_count, int(doc.page_count))):
            page = doc.load_page(i)
            if not (page.get_text("text") or "").strip() and page.get_images():
                degraded.add("ocr")
                break
    return degraded
