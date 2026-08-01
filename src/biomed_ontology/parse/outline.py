"""标题候选：三源汇合 + 置信度 + 证据。

复刻自 knowhere 的 `H1Candidate(source, evidence)`，但做了一处关键转写：
knowhere 把它当作内部中间态，**这里它同时是一条 `DecisionRecord`** ——
于是"这个标题的层级是怎么定的"变成可审计的 WHY，接上已有的四支柱。

三个来源按可信度排序：PDF 内嵌 TOC > 版面信号（字号/字重）> 正则模式。
冲突时高可信度胜出，但**低可信度的证据不丢弃**，一并记进 evidence——
排查"为什么这节被判成 H2"时，被否决的候选往往才是关键线索。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_concept import MappingJustificationEnum
from biomed_ontology._generated.hmd_fact import HeadingSourceEnum
from biomed_ontology.observability import Candidate, TraceContext
from biomed_ontology.parse.layout.base import LayoutResult

__all__ = ["HeadingCandidate", "extract_toc_nodes", "grep_headings", "merge_candidates"]

# IMRaD 及专利的常见章节名。命中不代表一定是标题，只是抬高置信度。
_CANONICAL = re.compile(
    r"^\s*(abstract|introduction|background|methods?|materials?\s+and\s+methods?|results?|"
    r"discussion|conclusions?|references?|acknowledg(e)?ments?|supplementary|"
    r"摘要|背景技术|发明内容|技术领域|具体实施方式|附图说明|权利要求书?|参考文献)\b",
    re.IGNORECASE,
)
# "3.2 Pharmacokinetics" / "二、发明内容" 这类显式编号
_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*)[.、\s]\s*(\S.*)$")
_CJK_NUMBERED = re.compile(r"^\s*([一二三四五六七八九十]+)\s*[、.]\s*(\S.*)$")
_HASHES = re.compile(r"^(#{1,6})\s+")


@dataclass(frozen=True)
class HeadingCandidate:
    title: str
    page: int
    level: int
    confidence: float
    source: HeadingSourceEnum
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        """归并键：标题文本归一化。同一标题被多源提出时才能合票。"""
        return re.sub(r"\s+", " ", self.title).strip().casefold()


def extract_toc_nodes(toc: list[list[object]]) -> list[HeadingCandidate]:
    """PDF 内嵌书签。作者显式声明的结构，可信度最高。"""
    out: list[HeadingCandidate] = []
    for entry in toc:
        if len(entry) < 3:
            continue
        level, title, page = int(entry[0]), str(entry[1]).strip(), int(entry[2])  # type: ignore[arg-type]
        if not title or page < 1:
            continue
        out.append(
            HeadingCandidate(
                title=title,
                page=page,
                level=max(1, level),
                confidence=0.95,
                source=HeadingSourceEnum.TOC_EXACT,
                evidence=(f"PDF 内嵌书签 level={level} page={page}",),
            )
        )
    return out


def grep_headings(layout: LayoutResult) -> list[HeadingCandidate]:
    """版面信号 + 正则。两者分别记为不同 source，便于事后归因。"""
    out: list[HeadingCandidate] = []
    for block in layout.headings():
        title = _HASHES.sub("", block.text).strip()
        if not title:
            continue
        level = block.level or 1
        evidence = [f"{layout.backend} 版面信号 level={level} page={block.page}"]
        conf = 0.72
        if (size := block.backend_meta.get("font_size")) is not None:
            evidence.append(f"字号 {float(size):.1f}")
        if block.backend_meta.get("bold"):
            evidence.append("粗体")
        if _CANONICAL.match(title):
            conf = 0.88
            evidence.append("命中标准章节名")
        out.append(
            HeadingCandidate(
                title=title,
                page=block.page,
                level=level,
                confidence=conf,
                source=HeadingSourceEnum.HEADING_REGEX,
                evidence=tuple(evidence),
            )
        )

    for block in layout.blocks:
        if block.kind != "text":
            continue
        cand = _pattern_candidate(block.text, block.page)
        if cand is not None:
            out.append(cand)
    return out


def _pattern_candidate(text: str, page: int) -> HeadingCandidate | None:
    text = text.strip()
    if len(text) > 80:
        return None
    if m := _NUMBERED.match(text):
        depth = m.group(1).count(".") + 1
        return HeadingCandidate(
            title=text,
            page=page,
            level=min(depth, 6),
            confidence=0.62,
            source=HeadingSourceEnum.HEADING_REGEX,
            evidence=(f"编号模式 {m.group(1)}",),
        )
    if m := _CJK_NUMBERED.match(text):
        return HeadingCandidate(
            title=text,
            page=page,
            level=1,
            confidence=0.60,
            source=HeadingSourceEnum.HEADING_REGEX,
            evidence=(f"中文编号 {m.group(1)}",),
        )
    if _CANONICAL.match(text) and len(text) <= 40:
        return HeadingCandidate(
            title=text,
            page=page,
            level=1,
            confidence=0.66,
            source=HeadingSourceEnum.HEADING_REGEX,
            evidence=("命中标准章节名",),
        )
    return None


def merge_candidates(
    *groups: list[HeadingCandidate],
    ctx: TraceContext | None = None,
) -> list[HeadingCandidate]:
    """按 (标题, 页码) 归并。同键多源 → 取最高置信度，但合并全部证据。

    多源互证会小幅抬高置信度（上限 0.98）——一个标题被 TOC 和版面同时认出，
    比只被其中一个认出更可信，但永远不到 1.0：没有哪种启发式配得上确定性。
    """
    buckets: dict[tuple[str, int], list[HeadingCandidate]] = {}
    for group in groups:
        for cand in group:
            buckets.setdefault((cand.key, cand.page), []).append(cand)

    merged: list[HeadingCandidate] = []
    for cands in buckets.values():
        best = max(cands, key=lambda c: (c.confidence, -c.level))
        evidence = tuple(dict.fromkeys(e for c in cands for e in c.evidence))
        sources = {c.source for c in cands}
        bonus = 0.06 if len(sources) > 1 else 0.0
        winner = HeadingCandidate(
            title=best.title,
            page=best.page,
            level=best.level,
            confidence=min(0.98, best.confidence + bonus),
            source=best.source,
            evidence=evidence,
        )
        merged.append(winner)
        if ctx is not None:
            ctx.record_decision(
                stage="parse.heading",
                justification=(
                    MappingJustificationEnum.CompositeMatching
                    if len(sources) > 1
                    else MappingJustificationEnum.LexicalMatching
                ),
                chosen=f"L{winner.level} {winner.title[:60]}",
                candidates=[
                    Candidate(
                        candidate_id=f"L{c.level} {c.title[:40]}",
                        score=c.confidence,
                        channel=c.source.value,
                        label="；".join(c.evidence) or None,
                    )
                    for c in cands
                ],
                confidence=winner.confidence,
                rule_id=winner.source.value,
            )

    merged.sort(key=lambda c: (c.page, c.level))
    return merged
