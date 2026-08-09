"""叶节点树 → 与手写语料**同一套 schema** 的产物。

这一步刻意不发明新格式：解析产物必须能通过 `test_corpus.py` 的全部断言，
手写的 `data/corpus/pipeline.yaml` 因此成为解析器的回归基准。
否则"解析出来了"和"解析对了"就没有区别。

`DocumentSection.name` 取 `section_path`（含 `/` 分隔的祖先链）而不是裸标题：
扁平字段里塞进层级信息，是为了让既有管线一行不改就能拿到导航能力。
"""

from __future__ import annotations

import re
from typing import Any

from biomed_ontology._generated.hmd_fact import HeadingSourceEnum
from biomed_ontology.corpus import Document, DocumentSection, ImageBlock, TableBlock
from biomed_ontology.parse.layout.base import LayoutResult
from biomed_ontology.parse.nodes import LeafNode, dedupe_same_as
from biomed_ontology.parse.skeleton import SectionSkeleton

__all__ = ["ParsedDocument", "emit_document"]

_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


class ParsedDocument:
    """解析产物。`to_yaml_obj()` 的输出与手写语料逐键同构。"""

    def __init__(
        self,
        document: Document,
        *,
        sections: list[dict[str, Any]],
        same_as: dict[str, str],
        degraded: tuple[str, ...],
        backend: str,
        route: dict[str, Any] | None = None,
    ) -> None:
        self.document = document
        self.sections = sections
        self.same_as = same_as
        self.degraded = degraded
        self.backend = backend
        self.route = route

    def to_yaml_obj(self) -> dict[str, Any]:
        parse: dict[str, Any] = {
            "backend": self.backend,
            # 空列表也要写出来：省略会让"没降级"和"没记录降级"分不清
            "degraded": list(self.degraded),
            "same_as": self.same_as,
        }
        if self.route is not None:
            parse["route"] = self.route
        return {
            "corpus_version": "0.1.0",
            "parse": parse,
            "sections_meta": self.sections,
            "documents": [self.document.model_dump(mode="json", exclude_none=True)],
        }


def emit_document(
    *,
    doc_id: str,
    source_id: str,
    title: str,
    doc_type: Any,
    license_tier: Any,
    language: Any,
    skeleton: list[SectionSkeleton],
    leaves: list[LeafNode],
    layout: LayoutResult,
    assets: dict[Any, Any] | None = None,
    external_id: str | None = None,
    published_on: str | None = None,
    route: dict[str, Any] | None = None,
) -> ParsedDocument:
    by_path = {s.section_path: s for s in skeleton}
    assets = assets or {}

    sections: list[DocumentSection] = []
    meta: list[dict[str, Any]] = []
    tables: list[TableBlock] = []
    images: list[ImageBlock] = []
    dedupe_input: list[tuple[str, str]] = []

    for order, leaf in enumerate(leaves):
        body = leaf.text.strip()
        skel = by_path.get(leaf.section_path)
        section_id = f"SEC:{doc_id.removeprefix('DOC:')}#{order:04d}"

        if body:
            sections.append(
                DocumentSection(name=leaf.section_path, text=body, page=leaf.start_page)
            )
            dedupe_input.append((section_id, body))

        meta.append(
            {
                "section_id": section_id,
                "doc_id": doc_id,
                "parent_section_id": None,
                "section_path": leaf.section_path,
                "section_title": leaf.title,
                "section_level": leaf.level,
                "sort_order": order,
                "start_page": leaf.start_page,
                "end_page": leaf.end_page,
                "heading_source": _source_value(skel),
                "heading_confidence": round(skel.heading_confidence if skel else 0.3, 3),
                "evidence": list(skel.evidence) if skel else [],
            }
        )

        for block in leaf.blocks:
            if block.kind == "table":
                tables.append(_table_block(block, len(tables), assets))
            elif block.kind == "image":
                images.append(_image_block(block, len(images), assets))

    _link_parents(meta)

    document = Document(
        doc_id=doc_id,
        source_id=source_id,
        external_id=external_id,
        title=title,
        doc_type=doc_type,
        published_on=published_on,
        language=language,
        license_tier=license_tier,
        sections=sections,
        tables=tables,
        images=images,
    )
    return ParsedDocument(
        document,
        sections=meta,
        same_as=dedupe_same_as(dedupe_input),
        degraded=layout.degraded,
        backend=layout.backend,
        route=route,
    )


def _source_value(skel: SectionSkeleton | None) -> str:
    src = skel.heading_source if skel else HeadingSourceEnum.SYNTHETIC
    return src.value


def _link_parents(meta: list[dict[str, Any]]) -> None:
    """按 section_path 前缀回填 parent_section_id。"""
    by_path = {m["section_path"]: m["section_id"] for m in meta}
    for m in meta:
        path = str(m["section_path"])
        if " / " in path:
            m["parent_section_id"] = by_path.get(path.rsplit(" / ", 1)[0])


def _table_block(block: Any, idx: int, assets: dict[Any, Any]) -> TableBlock:
    header, rows = _markdown_table(block.text)
    result = assets.get((block.page, tuple(block.bbox)))
    return TableBlock(
        table_id=f"TBL:{idx:04d}",
        caption=(result.summary or None) if result else None,
        page=block.page,
        bbox=list(block.bbox) or [0.0, 0.0, 0.0, 0.0],
        header=header,
        rows=rows,
        asset_path=result.rel_path if result else None,
    )


def _image_block(block: Any, idx: int, assets: dict[Any, Any]) -> ImageBlock:
    result = assets.get((block.page, tuple(block.bbox)))
    return ImageBlock(
        image_id=f"IMG:{idx:04d}",
        caption=block.text or None,
        page=block.page,
        bbox=list(block.bbox) or [0.0, 0.0, 0.0, 0.0],
        kind="figure",
        vision_summary=(result.summary or None) if result else None,
        # 只放行过了形状校验的值 —— sanitize 在 vision 层已经做过
        extracted_values=dict(result.extracted) if result else {},
        asset_path=result.rel_path if result else None,
    )


def _markdown_table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln for ln in text.splitlines() if _ROW.match(ln) and not _SEP.match(ln)]
    if not lines:
        return [], []
    parsed = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    return parsed[0], parsed[1:]
