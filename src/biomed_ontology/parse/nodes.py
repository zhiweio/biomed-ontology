"""骨架 + 版面块 → 叶节点，并做 SAME-AS 去重。

去重的关键决策：**重复内容不删，只指向 owner**。

论文的 Abstract 常与正文首段逐字重复，专利的权利要求书更是大段自我复制。
删掉重复会破坏 Citationware —— 用户拿到的碎片必须能还原回它**实际所在**的位置，
而不是被静默重定向到另一处。所以重复块保留自己的 `chunk_id` 与页码，
只多带一个 `same_as_chunk_id` 指向首现者，检索时可折叠、还原时仍准确。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from biomed_ontology.parse.layout.base import LayoutBlock
from biomed_ontology.parse.skeleton import SectionSkeleton

__all__ = ["LeafNode", "assign_blocks", "dedupe_same_as"]

_WS = re.compile(r"\s+")
_MIN_DEDUPE_CHARS = 60  # 短句撞车是常态（"Results"、"n=12"），不该被判为重复


@dataclass
class LeafNode:
    section_path: str
    title: str
    level: int
    start_page: int
    end_page: int
    parent_path: str | None
    blocks: list[LayoutBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.kind in {"text", "formula"})


def assign_blocks(
    skeleton: list[SectionSkeleton], blocks: tuple[LayoutBlock, ...]
) -> list[LeafNode]:
    """把版面块挂到最深的、页码范围覆盖它的章节上。

    按 `level` 降序找，因为父子章节的页码范围天然重叠 ——
    取最深的那个才是"这段文字实际属于哪一节"。
    """
    nodes = [
        LeafNode(
            section_path=s.section_path,
            title=s.title,
            level=s.level,
            start_page=s.start_page,
            end_page=s.end_page,
            parent_path=s.parent_path,
        )
        for s in skeleton
    ]
    by_depth = sorted(nodes, key=lambda n: -n.level)
    fallback = nodes[0] if nodes else None

    for block in blocks:
        if block.kind == "heading":
            continue  # 标题已经是节点本身，再入正文会让每节开头重复一遍标题
        target = next((n for n in by_depth if n.start_page <= block.page <= n.end_page), fallback)
        if target is not None:
            target.blocks.append(block)
    return nodes


def dedupe_same_as(
    items: list[tuple[str, str]],
) -> dict[str, str]:
    """`[(chunk_id, text)]` → `{重复的 chunk_id: 首现的 chunk_id}`。

    首现者不出现在返回值里 —— 它不指向任何人。
    """
    seen: dict[str, str] = {}
    same_as: dict[str, str] = {}
    for chunk_id, text in items:
        norm = _WS.sub(" ", text).strip().casefold()
        if len(norm) < _MIN_DEDUPE_CHARS:
            continue
        digest = hashlib.blake2b(norm.encode("utf-8"), digest_size=16).hexdigest()
        if (owner := seen.get(digest)) is not None:
            if owner != chunk_id:
                same_as[chunk_id] = owner
        else:
            seen[digest] = chunk_id
    return same_as
