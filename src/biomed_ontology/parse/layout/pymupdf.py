"""PyMuPDF 版面后端：进程内、零外部服务、无网络。

能力边界是**明说**的，不是让下游去猜：拿不到公式 LaTeX、不做 OCR、
表格结构弱于专用模型 —— 三件事都会写进 `LayoutResult.degraded`。

标题判定只靠字号与字重这类排版信号，**不做语义判断**。语义细化是
`skeleton.refine_fat_leaves` 的职责，两者分开才能各自被测。
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import MappingJustificationEnum
from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import Capability, LayoutBlock, LayoutResult

__all__ = ["PyMuPDFBackend"]

_BOLD = 1 << 4  # PyMuPDF span flags 的粗体位

# 公式的排版痕迹。命中即说明这份 PDF 里有 PyMuPDF 取不到的内容，
# 用来触发 degraded 声明，而不是用来抽公式本身。
_MATH_FONT = re.compile(r"CMMI|CMSY|CMEX|MSAM|MSBM|Math|Symbol", re.IGNORECASE)
_WS = re.compile(r"\s+")


class PyMuPDFBackend:
    name = "pymupdf"

    def __init__(self, *, max_pages: int = 400, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_pages = max_pages
        self.max_bytes = max_bytes

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in {".pdf", ".xps", ".epub"}

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        import pymupdf

        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"{path.name} 为 {size} 字节，超过上限 {self.max_bytes}")

        assets_dir = out_dir / "tables"
        assets_dir.mkdir(parents=True, exist_ok=True)

        with ctx.span("layout.pymupdf", doc=path.name) as span:
            doc = pymupdf.open(path)
            try:
                page_count = int(doc.page_count)
                if page_count > self.max_pages:
                    raise ValueError(f"{path.name} 共 {page_count} 页，超过上限 {self.max_pages}")
                blocks, degraded = self._walk(doc, out_dir)
            finally:
                doc.close()
            span.attributes["blocks"] = len(blocks)
            span.attributes["degraded"] = sorted(degraded)

        if degraded:
            ctx.record_decision(
                stage="parse.layout",
                justification=MappingJustificationEnum.UnspecifiedMatching,
                chosen=self.name,
                confidence=1.0,
                rule_id="layout.degraded",
                state_after=f"PyMuPDF 无法处理：{'、'.join(sorted(degraded))}；改用 MinerU 可补齐",
            )

        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=page_count,
            backend=self.name,
            degraded=tuple(sorted(degraded)),
        )

    # ------------------------------------------------------------------ 内部

    def _walk(self, doc: Any, out_dir: Path) -> tuple[list[LayoutBlock], set[Capability]]:
        degraded: set[Capability] = set()
        pages = [doc.load_page(i) for i in range(doc.page_count)]
        body_size = _body_font_size(pages)

        blocks: list[LayoutBlock] = []
        for page_no, page in enumerate(pages, start=1):
            table_boxes, table_blocks = self._tables(page, page_no, out_dir)
            blocks.extend(table_blocks)
            if table_blocks:
                degraded.add("table_structure")

            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") == 1:  # 图片：P11 才做渲染与 VLM
                    continue
                for line in blk.get("lines", ()):
                    parsed = _line_to_block(line, page_no, body_size)
                    if parsed is None:
                        continue
                    block, has_math = parsed
                    if has_math:
                        degraded.add("formula")
                    if _inside_any(block.bbox, table_boxes):
                        continue  # 表格文本已由 HTML 资产承载，重复入库会污染检索
                    blocks.append(block)

            if not page.get_text("text").strip() and page.get_images():
                degraded.add("ocr")  # 纯图页：有像素没文本

        return blocks, degraded

    def _tables(
        self, page: Any, page_no: int, out_dir: Path
    ) -> tuple[list[tuple[float, ...]], list[LayoutBlock]]:
        try:
            found = page.find_tables()
        except Exception:  # pragma: no cover - PyMuPDF 对畸形页会抛各种异常
            return [], []

        boxes: list[tuple[float, ...]] = []
        out: list[LayoutBlock] = []
        for idx, table in enumerate(found.tables):
            # 文件名只由页码与序号拼，绝不取自文档内容 —— 那是路径穿越入口
            rel = f"tables/p{page_no:04d}_t{idx}.html"
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(table.to_markdown(), encoding="utf-8")
            bbox = tuple(float(v) for v in table.bbox)
            boxes.append(bbox)
            out.append(
                LayoutBlock(
                    kind="table",
                    text=table.to_markdown(),
                    page=page_no,
                    bbox=bbox,
                    asset_path=rel,
                    backend_meta={"rows": table.row_count, "cols": table.col_count},
                )
            )
        return boxes, out


def _body_font_size(pages: list[Any]) -> float:
    """正文字号 = 全文字符数加权的中位数。标题判定是相对它的，不是绝对阈值。"""
    sizes: list[float] = []
    for page in pages[:20]:  # 前 20 页足够定标，全扫在长文档上不划算
        for blk in page.get_text("dict")["blocks"]:
            for line in blk.get("lines", ()):
                for span in line.get("spans", ()):
                    text = span.get("text", "").strip()
                    if text:
                        sizes.extend([float(span["size"])] * len(text))
    return statistics.median(sizes) if sizes else 10.0


def _line_to_block(
    line: dict[str, Any], page_no: int, body_size: float
) -> tuple[LayoutBlock, bool] | None:
    spans = [s for s in line.get("spans", ()) if s.get("text", "").strip()]
    if not spans:
        return None

    text = _WS.sub(" ", "".join(s["text"] for s in spans)).strip()
    if not text:
        return None

    has_math = any(_MATH_FONT.search(s.get("font", "")) for s in spans)
    size = max(float(s["size"]) for s in spans)
    bold = all(int(s.get("flags", 0)) & _BOLD for s in spans)
    bbox = tuple(float(v) for v in line["bbox"])

    level = _heading_level(text, size, body_size, bold=bold)
    if level is None:
        return LayoutBlock(kind="text", text=text, page=page_no, bbox=bbox), has_math
    return (
        LayoutBlock(
            kind="heading",
            text="#" * level + " " + text,
            page=page_no,
            bbox=bbox,
            level=level,
            backend_meta={"font_size": size, "bold": bold},
        ),
        has_math,
    )


def _heading_level(text: str, size: float, body: float, *, bold: bool) -> int | None:
    """字号比正文大多少 → 层级。返回 None 表示这是正文。

    长行一律不当标题：标题在版面上短，这条比任何字号阈值都稳。
    """
    if len(text) > 120 or text.endswith((".", "。", "；", ";")):
        return None
    ratio = size / body if body else 1.0
    if ratio >= 1.45:
        return 1
    if ratio >= 1.25:
        return 2
    if ratio >= 1.12:
        return 3
    if bold and ratio >= 1.02:
        return 4
    return None


def _inside_any(bbox: tuple[float, ...], boxes: list[tuple[float, ...]]) -> bool:
    if not bbox or not boxes:
        return False
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in boxes)
