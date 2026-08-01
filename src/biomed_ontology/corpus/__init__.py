"""语料治理层（L4）：解析 → 切片 → 标引分类 → 三模态抽取。

解析阶段必须保留 section 与 bbox。这不是锦上添花：
研究员核验一条"ORR 42.9%"时要看的是原文那张表的那一格，
只给 doc_id 的溯源在药物研发场景等同于没有溯源。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from biomed_ontology._generated.hmd_concept import LanguageEnum, LicenseTierEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum, ModalityChannelEnum

__all__ = [
    "Chunk",
    "Document",
    "DocumentSection",
    "TableBlock",
    "chunk_document",
    "load_corpus",
]


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    col: int
    text: str


class TableBlock(BaseModel):
    """视觉模型还原后的表格结构。bbox 保留在表级与单元格级，供溯源下钻。"""

    model_config = ConfigDict(extra="forbid")

    table_id: str
    caption: str | None = None
    page: int = 1
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)

    def cells(self) -> list[TableCell]:
        return [
            TableCell(row=r, col=c, text=v)
            for r, row in enumerate(self.rows)
            for c, v in enumerate(row)
        ]

    def as_text(self) -> str:
        lines = [" | ".join(self.header)] if self.header else []
        lines += [" | ".join(r) for r in self.rows]
        return "\n".join(lines)


class ImageBlock(BaseModel):
    """图像块。PoC 不做真实视觉推理，用 caption + 视觉模型输出的结构化描述占位。"""

    model_config = ConfigDict(extra="forbid")

    image_id: str
    caption: str | None = None
    page: int = 1
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    kind: str = "figure"
    vision_summary: str | None = None
    extracted_values: dict[str, str] = Field(default_factory=dict)


class DocumentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    text: str
    page: int = 1


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_id: str
    external_id: str | None = None
    title: str
    doc_type: DocTypeEnum
    published_on: str | None = None
    language: LanguageEnum = LanguageEnum.en
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[TableBlock] = Field(default_factory=list)
    images: list[ImageBlock] = Field(default_factory=list)

    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.sections)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    section: str
    char_start: int
    char_end: int
    modality: ModalityChannelEnum
    page: int = 1
    bbox: list[float] = field(default_factory=list)
    source_ref: str | None = None
    """表格/图像切片对应的 table_id 或 image_id，供下钻定位。"""

    concept_ids: list[str] = field(default_factory=list)
    concept_ids_expanded: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


class CorpusFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    documents: list[Document]
    # 解析产物的溯源。手写语料省略它；机器解析的必须带上 ——
    # 反之就无法回答"这批切片是哪个后端、在什么能力下产出的"。
    parse: dict[str, Any] | None = None
    sections_meta: list[dict[str, Any]] = Field(default_factory=list)


def load_corpus(path: Path) -> list[Document]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CorpusFile.model_validate(raw).documents


# ---------------------------------------------------------------- 切片

_SENT_SPLIT = re.compile(r"(?<=[.。!?！？])\s+")


def chunk_document(doc: Document, *, max_chars: int = 600) -> list[Chunk]:
    """按 section 切片，句子边界对齐。

    不做跨 section 的滑窗：section 边界通常也是语义边界，
    跨界拼接会让"Methods 的剂量"和"Results 的疗效"落进同一片，抽取时张冠李戴。
    """
    chunks: list[Chunk] = []
    offset = 0
    for sec in doc.sections:
        for text, start, end in _split_section(sec.text, max_chars):
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(doc.doc_id, "txt", offset + start),
                    doc_id=doc.doc_id,
                    text=text,
                    section=sec.name,
                    char_start=offset + start,
                    char_end=offset + end,
                    modality=ModalityChannelEnum.TEXT,
                    page=sec.page,
                )
            )
        offset += len(sec.text) + 2

    for t in doc.tables:
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(doc.doc_id, "tbl", hash(t.table_id) & 0xFFFFFF),
                doc_id=doc.doc_id,
                text=(f"{t.caption}\n" if t.caption else "") + t.as_text(),
                section=f"table:{t.table_id}",
                char_start=0,
                char_end=0,
                modality=ModalityChannelEnum.TABLE,
                page=t.page,
                bbox=list(t.bbox),
                source_ref=t.table_id,
            )
        )

    for im in doc.images:
        body = " ".join(filter(None, [im.caption, im.vision_summary]))
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(doc.doc_id, "img", hash(im.image_id) & 0xFFFFFF),
                doc_id=doc.doc_id,
                text=body,
                section=f"image:{im.image_id}",
                char_start=0,
                char_end=0,
                modality=ModalityChannelEnum.IMAGE,
                page=im.page,
                bbox=list(im.bbox),
                source_ref=im.image_id,
            )
        )
    return chunks


def _split_section(text: str, max_chars: int) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    buf, buf_start, cursor = "", 0, 0
    for sent in _SENT_SPLIT.split(text):
        if not sent:
            continue
        start = text.find(sent, cursor)
        if start < 0:
            start = cursor
        if buf and len(buf) + len(sent) > max_chars:
            out.append((buf.strip(), buf_start, buf_start + len(buf)))
            buf, buf_start = "", start
        if not buf:
            buf_start = start
        buf += ("" if not buf else " ") + sent
        cursor = start + len(sent)
    if buf.strip():
        out.append((buf.strip(), buf_start, buf_start + len(buf)))
    return out


def _chunk_id(doc_id: str, kind: str, seed: Any) -> str:
    h = hashlib.sha1(f"{doc_id}|{kind}|{seed}".encode()).hexdigest()[:10]
    return f"CHK:{kind}.{h}"
