"""标题候选 → 章节骨架。

两件事最容易出错，因此都被单独测：
1. **层级跳跃**（H1 直接跳 H3）。真实论文里很常见，多半是版面信号误判。
   处理方式是压平到父级 +1，而不是凭空插一个假的中间节点 ——
   凭空插节点会让 `section_path` 指向一个原文里根本不存在的章节。
2. **胖叶子**（一个叶节点吃掉几十页）。说明这一段的内部结构没被识别出来，
   `refine_fat_leaves` 负责标记它们，交给后续（P11 视觉/LLM）细化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_fact import HeadingSourceEnum
from biomed_ontology.parse.outline import HeadingCandidate

__all__ = ["SectionSkeleton", "build_skeleton", "fat_leaves"]

_PATH_SEP = " / "
_UNSAFE = re.compile(r"\s+")


@dataclass
class SectionSkeleton:
    section_path: str
    title: str
    level: int
    start_page: int
    end_page: int
    parent_path: str | None = None
    heading_source: HeadingSourceEnum = HeadingSourceEnum.SYNTHETIC
    heading_confidence: float = 0.5
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def page_span(self) -> int:
        return max(0, self.end_page - self.start_page) + 1


def build_skeleton(
    candidates: list[HeadingCandidate],
    *,
    page_count: int,
    root_title: str = "Document",
) -> list[SectionSkeleton]:
    """把有序候选串成树。空候选 → 单个合成根节点，而不是空列表。

    返回空列表会让下游"没有章节"和"解析失败"两种情况长得一样；
    一个 SYNTHETIC 根节点则明说了"这份文档没识别出结构"。
    """
    if not candidates:
        return [
            SectionSkeleton(
                section_path=root_title,
                title=root_title,
                level=1,
                start_page=1,
                end_page=max(1, page_count),
                heading_source=HeadingSourceEnum.SYNTHETIC,
                heading_confidence=0.30,
                evidence=("未识别到任何标题，全文归为单一合成章节",),
            )
        ]

    ordered = sorted(candidates, key=lambda c: (c.page, c.level))
    normalized = _repair_levels(ordered)

    out: list[SectionSkeleton] = []
    stack: list[SectionSkeleton] = []
    for cand, level in normalized:
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        title = _clean(cand.title)
        path = f"{parent.section_path}{_PATH_SEP}{title}" if parent else title
        node = SectionSkeleton(
            section_path=_dedupe_path(path, {s.section_path for s in out}),
            title=title,
            level=level,
            start_page=cand.page,
            end_page=max(1, page_count),
            parent_path=parent.section_path if parent else None,
            heading_source=cand.source,
            heading_confidence=cand.confidence,
            evidence=cand.evidence,
        )
        out.append(node)
        stack.append(node)

    _close_ranges(out, page_count)
    return out


def _repair_levels(ordered: list[HeadingCandidate]) -> list[tuple[HeadingCandidate, int]]:
    """把层级跳跃压平到 父级+1。记录原始层级供排查。"""
    out: list[tuple[HeadingCandidate, int]] = []
    prev = 0
    for cand in ordered:
        level = cand.level if cand.level <= prev + 1 else prev + 1
        out.append((cand, max(1, level)))
        prev = max(1, level)
    return out


def _close_ranges(nodes: list[SectionSkeleton], page_count: int) -> None:
    """每节结束于**下一个同级或更高级标题的前一页**，而不是下一个标题的前一页。

    后者会把子章节从父章节里挖掉，导致父章节的 end_page < start_page。
    """
    for i, node in enumerate(nodes):
        end = max(1, page_count)
        for nxt in nodes[i + 1 :]:
            if nxt.level <= node.level:
                end = max(node.start_page, nxt.start_page - 1)
                break
        node.end_page = end


def _clean(title: str) -> str:
    return _UNSAFE.sub(" ", title).strip().strip("#").strip() or "Untitled"


def _dedupe_path(path: str, existing: set[str]) -> str:
    """同名同级章节（如多个 "Table"）必须路径唯一，否则 section_path 主键会碰撞。"""
    if path not in existing:
        return path
    n = 2
    while f"{path} #{n}" in existing:
        n += 1
    return f"{path} #{n}"


def fat_leaves(nodes: list[SectionSkeleton], *, max_pages: int = 12) -> list[SectionSkeleton]:
    """跨页过多的叶节点。它们是"内部结构没识别出来"的信号，不是错误。"""
    parents = {n.parent_path for n in nodes if n.parent_path}
    return [n for n in nodes if n.section_path not in parents and n.page_span > max_pages]
