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
    asset_path: str | None = None
    """渲染出的图像相对路径，供多模态向量列读取像素。手写语料没有。"""

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
    """图像块。caption 与 VLM 摘要是它的文本面，`asset_path` 是它的像素面。"""

    model_config = ConfigDict(extra="forbid")

    image_id: str
    caption: str | None = None
    page: int = 1
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    kind: str = "figure"
    vision_summary: str | None = None
    extracted_values: dict[str, str] = Field(default_factory=dict)
    asset_path: str | None = None
    """渲染出的图像相对路径，供多模态向量列读取像素。手写语料没有。"""


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

    asset_path: str | None = None
    """图像切片的像素在哪。文本切片为 None —— 多模态列据此决定看图还是读字。"""

    figure_type: str = ""
    """图型（RADIOLOGY / MICROSCOPY / CHART / ...），由 `parse.figure_type` 打上。

    空串是"未分类"，不是"不是图"—— 后者看 `modality`。这两件事必须分得开：
    没跑过分类器的库里所有图都是空串，此时按图型过滤会一条都返回不了，
    那是缺分类不是缺图。
    """

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


# Milvus 的 VARCHAR 上限按 UTF-8 字节算，而中文一个字占 3 字节。
# 留出余量而不是顶着 8192 写，是因为超限的失败形态是整批写入回滚，不是丢一条。
_TABLE_MAX_BYTES = 6000


def _split_table(t: TableBlock, *, max_chars: int) -> list[str]:
    """长表按行切片。表头在每一片里重复，否则后半张表的数字没有列名可依。

    单条巨型向量表达不了一张几十行的表 —— 它只会得到一个谁都不像的平均。
    """
    header = " | ".join(t.header)
    rows = [" | ".join(r) for r in t.rows]
    if not rows:
        return [header]

    budget = max(max_chars - len(header.encode()), 512)
    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for row in rows:
        n = len(row.encode()) + 1
        if cur and size + n > budget:
            parts.append("\n".join([header, *cur]) if header else "\n".join(cur))
            cur, size = [], 0
        cur.append(row)
        size += n
    if cur:
        parts.append("\n".join([header, *cur]) if header else "\n".join(cur))
    return parts


# 论文的"包装纸"：署名、致谢、利益冲突、参考文献表。
# 它们对"这个药疗效如何"一类问题永远不是答案，却因为词汇高度重合而挤进前十。
_BOILERPLATE = re.compile(
    r"(reference|bibliograph|acknowledg|author\s*contribution|conflict\s*of\s*interest"
    r"|competing\s*interest|disclosure|funding|data\s*availability|ethics\s*statement"
    r"|publisher.s\s*note|supplementary|abbreviation|additional\s*information"
    r"|参考文献|致谢|利益冲突|作者贡献)",
    re.IGNORECASE,
)


def _is_boilerplate(section: str) -> bool:
    """按 section 路径的**末段**判断。

    路径是 `父 / 子` 拼出来的，拿整条串匹配会因为祖先叫 "References" 就把
    它下面的所有内容一起误杀 —— 版面错切时这种嵌套很常见。
    """
    return bool(_BOILERPLATE.search(section.rsplit("/", 1)[-1]))


def chunk_document(doc: Document, *, max_chars: int = 600) -> list[Chunk]:
    """按 section 切片，句子边界对齐。

    不做跨 section 的滑窗：section 边界通常也是语义边界，
    跨界拼接会让"Methods 的剂量"和"Results 的疗效"落进同一片，抽取时张冠李戴。
    """
    chunks: list[Chunk] = []
    offset = 0
    for sec in doc.sections:
        if _is_boilerplate(sec.name):
            # offset 照常推进：切片 ID 的种子是全文偏移，跳过不等于当它不存在，
            # 否则删一节参考文献会把后面所有正文切片的 ID 全部平移。
            offset += len(sec.text) + 2
            continue
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
        head = f"{t.caption}\n" if t.caption else ""
        for part, body in enumerate(_split_table(t, max_chars=_TABLE_MAX_BYTES - len(head))):
            # 第 0 片沿用 table_id 作种子，长表增行时不会把已有切片 ID 全冲掉。
            seed = t.table_id if part == 0 else f"{t.table_id}#{part}"
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(doc.doc_id, "tbl", seed),
                    doc_id=doc.doc_id,
                    text=head + body,
                    section=f"table:{t.table_id}",
                    char_start=0,
                    char_end=0,
                    modality=ModalityChannelEnum.TABLE,
                    page=t.page,
                    bbox=list(t.bbox),
                    source_ref=t.table_id,
                    asset_path=t.asset_path,
                )
            )

    for im in doc.images:
        body = " ".join(filter(None, [im.caption, im.vision_summary]))
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(doc.doc_id, "img", im.image_id),
                doc_id=doc.doc_id,
                text=body,
                section=f"image:{im.image_id}",
                char_start=0,
                char_end=0,
                modality=ModalityChannelEnum.IMAGE,
                page=im.page,
                bbox=list(im.bbox),
                source_ref=im.image_id,
                asset_path=im.asset_path,
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
    """`seed` 必须是稳定值。内置 `hash()` 逐进程随机，会让切片 ID 每次重建都变 ——
    索引随即失配，已发出的引用也解不开。"""
    h = hashlib.sha1(f"{doc_id}|{kind}|{seed}".encode()).hexdigest()[:10]
    return f"CHK:{kind}.{h}"
