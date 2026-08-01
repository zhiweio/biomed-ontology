"""版面后端的统一中间表示。

knowhere 的解析边界是**纯 Markdown**（只消费 MinerU 的 `full.md`，
`content_list.json` 根本不解析），其 FAQ 原话是 "Any Markdown-outputting tool works."。
这里刻意不照抄那条边界：纯 Markdown 会丢掉 bbox，而引用优先（Citationware）
要求碎片能还原到原文 PDF 的具体位置。所以中间表示是 **Markdown 文本 + 逐块 provenance**。

`degraded` 是本模块最要紧的设计：PyMuPDF 拿不到公式 LaTeX，也不做 OCR。
与其让下游以为拿到了完整信息，不如显式声明这一次少了什么，并让它随切片
一路透传到 agent 手里 —— 沉默的能力缺失比明说的降级危险得多。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from biomed_ontology.observability import TraceContext

__all__ = ["BlockKind", "Capability", "LayoutBackend", "LayoutBlock", "LayoutResult"]

BlockKind = Literal["text", "heading", "table", "image", "formula"]

# 可能缺失的能力。取值受限是为了让 degraded 可被断言、可被聚合统计，
# 而不是变成一堆各写各的自由文本。
Capability = Literal["bbox", "formula", "ocr", "table_structure", "reading_order"]


@dataclass(frozen=True)
class LayoutBlock:
    kind: BlockKind
    text: str
    page: int
    """1-based **原始文档**页码。分片偏移的换算责任在后端内部，下游永远看到原始页码。"""

    bbox: tuple[float, ...] = ()
    """[x0, y0, x1, y1]。拿不到时留空元组，绝不伪造成整页坐标。"""

    level: int | None = None
    asset_path: str | None = None
    backend_meta: dict[str, object] = field(default_factory=dict)
    """后端专有信息，仅供排查。下游不得依赖 —— 依赖了就等于绑死某一个后端。"""


@dataclass(frozen=True)
class LayoutResult:
    blocks: tuple[LayoutBlock, ...]
    assets_dir: Path
    page_count: int
    backend: str
    degraded: tuple[Capability, ...] = ()

    def headings(self) -> tuple[LayoutBlock, ...]:
        return tuple(b for b in self.blocks if b.kind == "heading")


@runtime_checkable
class LayoutBackend(Protocol):
    name: str

    def supports(self, path: Path) -> bool: ...

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult: ...
