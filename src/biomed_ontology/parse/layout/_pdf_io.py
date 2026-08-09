"""PDF 底层 IO：probe / TOC / 打开校验。

仅供 Fast Path 与 Router 内部使用；**不是** LayoutBackend。
`parse.backend` 不得写出 `pymupdf`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["PdfProbe", "open_pdf", "probe_pdf", "read_toc"]


@dataclass(frozen=True)
class PdfProbe:
    page_count: int
    image_count: int
    table_candidates: int
    text_chars: int
    avg_blocks_per_page: float
    multi_column_hint: bool
    text_extractable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "page_count": self.page_count,
            "image_count": self.image_count,
            "table_candidates": self.table_candidates,
            "text_chars": self.text_chars,
            "avg_blocks_per_page": self.avg_blocks_per_page,
            "multi_column_hint": self.multi_column_hint,
            "text_extractable": self.text_extractable,
        }


def open_pdf(path: Path, *, max_pages: int, max_bytes: int) -> Any:
    import pymupdf

    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path.name} 为 {size} 字节，超过上限 {max_bytes}")
    doc = pymupdf.open(path)
    try:
        page_count = int(doc.page_count)
    except Exception:
        doc.close()
        raise
    if page_count > max_pages:
        doc.close()
        raise ValueError(f"{path.name} 共 {page_count} 页，超过上限 {max_pages}")
    return doc


def read_toc(path: Path) -> list[list[object]]:
    """内嵌书签；打不开或不支持则空列表。"""
    try:
        import pymupdf
    except ImportError:
        return []
    try:
        with pymupdf.open(path) as doc:
            return list(doc.get_toc())
    except Exception:
        return []


def probe_pdf(path: Path, *, max_pages: int = 400, max_bytes: int = 64 * 1024 * 1024) -> PdfProbe:
    """廉价信号，不跑全文语义树。最多扫前 20 页。"""
    doc = open_pdf(path, max_pages=max_pages, max_bytes=max_bytes)
    try:
        page_count = int(doc.page_count)
        sample_n = min(page_count, 20)
        image_count = 0
        table_candidates = 0
        text_chars = 0
        block_counts: list[int] = []
        multi_hits = 0
        for i in range(sample_n):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            text_chars += len(text.strip())
            blocks = page.get_text("dict").get("blocks") or []
            block_counts.append(len(blocks))
            image_count += sum(1 for b in blocks if b.get("type") == 1)
            try:
                tables = page.find_tables()
                table_candidates += len(getattr(tables, "tables", ()) or ())
            except Exception:
                pass
            if _looks_multi_column(page):
                multi_hits += 1
        avg_blocks = (sum(block_counts) / len(block_counts)) if block_counts else 0.0
        text_extractable = (text_chars / max(sample_n, 1)) >= 40
        return PdfProbe(
            page_count=page_count,
            image_count=image_count,
            table_candidates=table_candidates,
            text_chars=text_chars,
            avg_blocks_per_page=avg_blocks,
            multi_column_hint=multi_hits >= max(1, sample_n // 3),
            text_extractable=text_extractable,
        )
    finally:
        doc.close()


def _looks_multi_column(page: Any) -> bool:
    """粗启发式：正文块中心落在左半与右半的都够多。"""
    width = float(page.rect.width) or 1.0
    mid = width / 2
    left = right = 0
    for blk in page.get_text("dict").get("blocks") or []:
        if blk.get("type") != 0:
            continue
        bbox = blk.get("bbox") or ()
        if len(bbox) != 4:
            continue
        cx = (float(bbox[0]) + float(bbox[2])) / 2
        if cx < mid - 20:
            left += 1
        elif cx > mid + 20:
            right += 1
    return left >= 3 and right >= 3
