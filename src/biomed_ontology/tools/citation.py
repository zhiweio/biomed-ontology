"""Citationware：引用优先的还原。

检索返回的是高匹配度碎片。碎片能证明"有这句话"，却证明不了"在什么语境下说的" ——
而临床结论的语境（哪一组、哪个终点、哪次随访）恰恰决定它成不成立。
这一层负责从碎片走回原文：拼回整节、给出面包屑、报出原始页码。

许可谓词在这里同样生效。还原若绕过许可，就成了一个用碎片 id 换全文的后门。

正文权威源是 ``ChunkStore``（生产为 Iceberg ``evidence_chunks``），不是进程内 ``kb.chunks``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_tools import RestoreScopeEnum

if TYPE_CHECKING:  # pragma: no cover
    from biomed_ontology.lake.chunk_store import ChunkStore
    from biomed_ontology.pipeline import KnowledgeBase

__all__ = [
    "RestoredContext",
    "build_evidence_tree",
    "restore_context",
]

_SEP = " / "


@dataclass
class RestoredContext:
    doc_id: str
    section_id: str
    section_path: str
    breadcrumb: str
    full_text: str
    page_start: int
    page_end: int
    sibling_paths: list[str] = field(default_factory=list)
    truncated: bool = False
    restored_chunk_ids: list[str] = field(default_factory=list)
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0


def restore_context(
    kb: KnowledgeBase | None,
    chunk_id: str,
    *,
    store: ChunkStore,
    scope: RestoreScopeEnum | str = RestoreScopeEnum.SECTION,
    max_chars: int = 8000,
    permits: Any = None,
) -> RestoredContext:
    """从一个碎片还原到它所在的原文。

    `permits(license_rank, source_id) -> bool` 由调用方注入，
    复用 `LicenseScope.permits` 那一个谓词 —— 这里再写一份判断
    就会出现"检索看不到但还原看得到"的越权。

    正文经 ``store`` 拉取；``kb`` 仅用于面包屑标题（可缺省）。
    """
    scope = RestoreScopeEnum(scope) if isinstance(scope, str) else scope
    anchor = store.get_chunk(chunk_id)
    if anchor is None:
        raise KeyError(f"未知切片：{chunk_id}")

    tier = _tier_of(anchor)
    source_id = str(anchor.source_id or "")
    if permits is not None and not permits(_rank(tier), source_id):
        raise PermissionError(f"无权还原该文档：{anchor.document_id}")

    members = _members(store, anchor, scope)
    text, truncated = _join(members, max_chars)
    pages = [int(c.page or 0) for c in members if c.page]
    section = _section_of(anchor)

    return RestoredContext(
        doc_id=anchor.document_id,
        section_id=section,
        section_path=section,
        breadcrumb=_breadcrumb(kb, anchor, section),
        full_text=text,
        page_start=min(pages) if pages else 0,
        page_end=max(pages) if pages else 0,
        sibling_paths=_siblings(store, anchor),
        truncated=truncated,
        restored_chunk_ids=[c.chunk_id for c in members],
        license_tier=tier,
    )


def _tier_of(record: Any) -> LicenseTierEnum:
    raw = getattr(record, "license_tier", None)
    if isinstance(raw, LicenseTierEnum):
        return raw
    try:
        return LicenseTierEnum(str(raw or "TIER_0"))
    except ValueError:
        return LicenseTierEnum.TIER_3


def _section_of(chunk: Any) -> str:
    return str(
        getattr(chunk, "section_path", None) or getattr(chunk, "section", "") or ""
    )


def _rank(tier: LicenseTierEnum) -> int:
    from biomed_ontology.licensing import tier_rank

    return tier_rank(tier)


def _members(store: ChunkStore, anchor: Any, scope: RestoreScopeEnum) -> list[Any]:
    doc_id = anchor.document_id
    if scope is RestoreScopeEnum.DOCUMENT:
        return list(store.get_document_chunks(doc_id))
    if scope is RestoreScopeEnum.SIBLINGS:
        parent = _parent(_section_of(anchor))
        return [
            c
            for c in store.get_document_chunks(doc_id)
            if _parent(_section_of(c)) == parent
        ]
    return list(store.get_section_chunks(doc_id, _section_of(anchor)))


def _parent(section_path: str) -> str:
    return section_path.rsplit(_SEP, 1)[0] if _SEP in section_path else ""


def _breadcrumb(kb: KnowledgeBase | None, anchor: Any, section_path: str) -> str:
    title = str(getattr(anchor, "title", "") or "")
    if not title and kb is not None:
        doc = kb.document(anchor.document_id)
        title = getattr(doc, "title", "") if doc else ""
    if not title:
        title = anchor.document_id
    return _SEP.join(filter(None, [title, section_path]))


def _siblings(store: ChunkStore, anchor: Any) -> list[str]:
    parent = _parent(_section_of(anchor))
    here = _section_of(anchor)
    paths = {
        _section_of(c)
        for c in store.get_document_chunks(anchor.document_id)
        if _parent(_section_of(c)) == parent
    }
    return sorted(p for p in paths if p and p != here)


def _join(members: list[Any], max_chars: int) -> tuple[str, bool]:
    """超限就截断并如实报告。静默丢内容会让"还原完整原文"变成一句假话。"""
    parts, total = [], 0
    for chunk in members:
        text = str(getattr(chunk, "text", None) or getattr(chunk, "content", "") or "")
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                parts.append(text[:remaining])
            return "\n\n".join(parts), True
        parts.append(text)
        total += len(text) + 2
    return "\n\n".join(parts), False


def build_evidence_tree(kb: KnowledgeBase, hits: list[Any]) -> list[dict[str, Any]]:
    """把扁平命中聚合成 文档 → 章节 → 碎片 的证据树。

    扁平列表里同一文档的 5 个碎片看起来像 5 条独立证据，
    实际上它们可能全出自同一段 —— 这种"证据量的错觉"会直接误导判断。
    """
    docs: dict[str, dict[str, Any]] = {}
    for hit in hits:
        doc = kb.document(hit.doc_id) if kb is not None else None
        node = docs.setdefault(
            hit.doc_id,
            {
                "doc_id": hit.doc_id,
                "title": getattr(doc, "title", "") if doc else "",
                "license_tier": hit.license_tier.value,
                "sections": {},
                "chunk_count": 0,
                "best_score": 0.0,
            },
        )
        section = node["sections"].setdefault(
            hit.section or "",
            {"section_path": hit.section or "", "pages": set(), "chunks": []},
        )
        section["chunks"].append(
            {
                "chunk_id": hit.chunk_id,
                "page": hit.page,
                "score": round(hit.score, 6),
                "retrieval_channel": hit.channel.value,
                "snippet": hit.snippet,
            }
        )
        if hit.page:
            section["pages"].add(hit.page)
        node["chunk_count"] += 1
        node["best_score"] = max(node["best_score"], hit.score)

    tree = []
    for node in sorted(docs.values(), key=lambda d: -d["best_score"]):
        sections = []
        for sec in node["sections"].values():
            pages = sorted(sec["pages"])
            sections.append(
                {
                    "section_path": sec["section_path"],
                    "page_start": pages[0] if pages else 0,
                    "page_end": pages[-1] if pages else 0,
                    "chunks": sec["chunks"],
                }
            )
        node["sections"] = sorted(sections, key=lambda s: s["page_start"])
        node["best_score"] = round(node["best_score"], 6)
        tree.append(node)
    return tree
