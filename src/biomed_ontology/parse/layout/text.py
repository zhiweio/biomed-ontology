"""纯文本 / Markdown 后端：不走版面引擎，声明能力缺失。"""

from __future__ import annotations

import re
from pathlib import Path

from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import Capability, LayoutBlock, LayoutResult

__all__ = ["TextBackend"]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SUFFIXES = {".txt", ".md"}
_DEGRADED: tuple[Capability, ...] = ("bbox", "ocr", "formula", "table_structure")


class TextBackend:
    name = "text"

    def __init__(self, *, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in _SUFFIXES

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"{path.name} 为 {size} 字节，超过上限 {self.max_bytes}")
        out_dir.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8")
        with ctx.span("layout.text", doc=path.name) as span:
            blocks = _from_markdown(text) if path.suffix.casefold() == ".md" else _from_plain(text)
            span.attributes["blocks"] = len(blocks)
            span.attributes["degraded"] = list(_DEGRADED)
        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=1,
            backend=self.name,
            degraded=_DEGRADED,
        )


def _from_plain(text: str) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        body = para.strip()
        if body:
            blocks.append(LayoutBlock(kind="text", text=body, page=1))
    if not blocks and text.strip():
        blocks.append(LayoutBlock(kind="text", text=text.strip(), page=1))
    return blocks


def _from_markdown(text: str) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    buf: list[str] = []

    def _flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            blocks.append(LayoutBlock(kind="text", text=body, page=1))

    for raw in text.replace("\r\n", "\n").split("\n"):
        m = _HEADING.match(raw)
        if m:
            _flush()
            blocks.append(
                LayoutBlock(kind="heading", text=m.group(2).strip(), page=1, level=len(m.group(1)))
            )
            continue
        if not raw.strip():
            _flush()
            continue
        buf.append(raw)
    _flush()
    return blocks
