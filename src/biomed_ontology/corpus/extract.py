"""三模态抽取（L4）：文本 / 表格 / 图像。

三个通道分开实现而不是塞进一个通用抽取器，因为它们的错误模式完全不同：
文本错在关系方向，表格错在行列对齐，图像错在数值读取。
混在一起就无法分模态统计准确率，也就无法判断该修哪一段管线。

PoC 用 schema-guided 的规则抽取器跑通方法论与质量体系；
`FactExtractor` 是 Protocol，生产接本地垂类模型 / DeepSeek / 千问视觉时按同一接口替换。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from biomed_ontology._generated.hmd_concept import (
    LicenseTierEnum,
    PredicateEnum,
    ReviewStatusEnum,
)
from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
from biomed_ontology.corpus import Chunk, Document
from biomed_ontology.normalize import Normalizer
from biomed_ontology.observability import TraceContext

__all__ = [
    "Evidence",
    "ExtractedFact",
    "FactExtractor",
    "ImageExtractor",
    "TableExtractor",
    "TextRelationExtractor",
    "TriModalPipeline",
]


@dataclass(frozen=True)
class Evidence:
    chunk_id: str
    doc_id: str
    section: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    page: int | None = None
    bbox: tuple[float, ...] = ()
    modality: ModalityChannelEnum = ModalityChannelEnum.TEXT
    quote: str | None = None


@dataclass
class ExtractedFact:
    fact_id: str
    subject_id: str
    predicate: PredicateEnum
    object_id: str | None = None
    object_value: str | None = None
    object_unit: str | None = None
    qualifiers: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.5
    extractor_id: str = "unknown"
    extractor_version: str = "0.1.0"
    review_status: ReviewStatusEnum = ReviewStatusEnum.PENDING
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0
    modality: ModalityChannelEnum = ModalityChannelEnum.TEXT

    def signature(self) -> tuple:
        return (self.subject_id, self.predicate.value, self.object_id, self.object_value)


class FactExtractor(Protocol):
    extractor_id: str
    modality: ModalityChannelEnum

    def extract(
        self, chunk: Chunk, doc: Document, normalizer: Normalizer, ctx: TraceContext
    ) -> list[ExtractedFact]: ...


# ---------------------------------------------------------------- 文本通道

# 触发词与谓词的映射。方向靠句式而非共现判定：
# "A inhibits B" 与 "B inhibits A" 共现完全相同，只看共现必然一半方向是错的。
#
# 捕获组一律用 [^,.;；，。] 限制，不允许跨子句：跨子句捕获会把
# "用于治疗非小细胞肺癌。该晶型经粉末衍射表征" 整段当作宾语，
# 归一化随后在噪声里瞎猜，错误就此隐入事实层。
_EN = r"[^,.;:()\[\]]{1,60}"
_ZH = r"[^，。；、（）]{1,40}"
_ZH0 = r"[^，。；、（）]{0,40}"

_TEXT_PATTERNS: list[tuple[re.Pattern[str], PredicateEnum]] = [
    # ---- 英文
    (
        re.compile(
            rf"(?P<s>{_EN}?)\s+(?:selectively\s+)?inhibit(?:s|ed|ing)?\s+(?P<o>{_EN})", re.I
        ),
        PredicateEnum.inhibits,
    ),
    (
        re.compile(
            rf"(?P<s>{_EN}?)\s+is\s+an?\s+(?:\w+\s+){{0,3}}?(?P<o>\S{{2,30}})\s+inhibitor", re.I
        ),
        PredicateEnum.inhibits,
    ),
    (
        re.compile(rf"(?P<s>{_EN}?)\s+target(?:s|ed|ing)?\s+(?P<o>{_EN})", re.I),
        PredicateEnum.has_target,
    ),
    (
        re.compile(rf"(?P<s>{_EN}?)\s+for\s+(?:the\s+)?treatment\s+of\s+(?P<o>{_EN})", re.I),
        PredicateEnum.treats,
    ),
    (
        re.compile(rf"(?P<s>{_EN}?)\s+(?:was|were)\s+evaluated\s+in\s+(?P<o>{_EN})", re.I),
        PredicateEnum.in_clinical_trial_for,
    ),
    # biomarker_for 的主语是标志物、宾语是被预测的药 —— 写反会让
    # "MET 是 savolitinib 的标志物" 变成 "savolitinib 是 MET 的标志物"，
    # 而后者在患者筛选场景里是彻底讲不通的一句话。
    (
        re.compile(rf"(?P<s>{_EN}?)\s+predict(?:s|ed)?\s+response\s+to\s+(?P<o>{_EN})", re.I),
        PredicateEnum.biomarker_for,
    ),
    (
        re.compile(rf"(?P<s>{_EN}?)\s+served\s+as\s+the\s+biomarker\s+for\s+(?P<o>{_EN})", re.I),
        PredicateEnum.biomarker_for,
    ),
    # ---- 中文（中文文献与专利占内部语料的相当比例，缺了这一组等于半个语料库不产事实）
    # 主语组允许为空：中文小句普遍省略主语（"X 是一种 MET 抑制剂，用于治疗 Y"
    # 的第二个小句没有主语），强制要求主语会让整类中文句式颗粒无收。
    (
        re.compile(
            rf"(?P<s>{_ZH0}?)是一种(?:[^，。]{{0,12}}?)(?P<o>[A-Za-z0-9\u4e00-\u9fff]{{2,20}})\s*抑制剂"
        ),
        PredicateEnum.inhibits,
    ),
    (re.compile(rf"(?P<s>{_ZH0}?)抑制(?P<o>{_ZH})"), PredicateEnum.inhibits),
    (re.compile(rf"(?P<s>{_ZH0}?)用于(?:制备)?治疗(?P<o>{_ZH})"), PredicateEnum.treats),
    (re.compile(rf"(?P<s>{_ZH0}?)靶向(?P<o>{_ZH})"), PredicateEnum.has_target),
]


class TextRelationExtractor:
    """schema-guided 文本关系抽取。谓词只能取 PredicateEnum 中已声明的值。"""

    extractor_id = "text-rule-v1"
    modality = ModalityChannelEnum.TEXT

    def extract(
        self, chunk: Chunk, doc: Document, normalizer: Normalizer, ctx: TraceContext
    ) -> list[ExtractedFact]:
        out: list[ExtractedFact] = []
        for sent in re.split(r"(?<=[.。!?])\s*", chunk.text):
            if not sent.strip():
                continue
            carried: str | None = None
            for pattern, predicate in _TEXT_PATTERNS:
                m = pattern.search(sent)
                if not m:
                    continue
                s_raw = m.group("s").strip()
                if not s_raw:
                    # 主语省略：承继本句已确立的主语。中文里这是常态，
                    # 不承继就等于把"，用于治疗非小细胞肺癌"这类小句全部丢掉。
                    carried = carried or self._sentence_subject(sent, normalizer, ctx)
                    s_id = carried
                else:
                    s_id = _ground(normalizer, s_raw, ctx, sent)
                    carried = carried or s_id
                o_id = _ground(normalizer, m.group("o"), ctx, sent)
                if not s_id or not o_id or s_id == o_id:
                    continue
                out.append(
                    ExtractedFact(
                        fact_id="",
                        subject_id=s_id,
                        predicate=predicate,
                        object_id=o_id,
                        confidence=0.72,
                        extractor_id=self.extractor_id,
                        modality=self.modality,
                        evidence=[
                            Evidence(
                                chunk_id=chunk.chunk_id,
                                doc_id=chunk.doc_id,
                                section=chunk.section,
                                char_start=chunk.char_start,
                                char_end=chunk.char_end,
                                page=chunk.page,
                                modality=self.modality,
                                quote=sent.strip()[:300],
                            )
                        ],
                    )
                )
        return out

    def _sentence_subject(
        self, sentence: str, normalizer: Normalizer, ctx: TraceContext
    ) -> str | None:
        """句首第一个可归一化实体。承继范围限定在单句内 —— 跨句承继会把
        上一句的药名安到下一句的化合物上，那是最难排查的一类静默错误。"""
        head = sentence.split("，")[0].split(",")[0]
        res = normalizer.normalize(head, ctx=ctx, detect=True, min_confidence=0.6)
        return res.matched[0].concept_id if res.matched else None


# ---------------------------------------------------------------- 表格通道

_METRIC_COLUMNS = {
    "orr": ("ORR", "%"),
    "objective response rate": ("ORR", "%"),
    "客观缓解率": ("ORR", "%"),
    "pfs": ("PFS", "months"),
    "median pfs": ("PFS", "months"),
    "无进展生存期": ("PFS", "months"),
    "os": ("OS", "months"),
    "median os": ("OS", "months"),
    "dcr": ("DCR", "%"),
    "ic50": ("IC50", "nM"),
}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


class TableExtractor:
    """表格通道。

    生产链路是：千问视觉模型还原表格结构 → 本抽取器做单元格语义对齐。
    PoC 直接消费已还原的结构，因为要验证的是"对齐到本体"这一步，
    而不是版面还原本身 —— 后者是成熟的商用能力。
    """

    extractor_id = "table-cell-align-v1"
    modality = ModalityChannelEnum.TABLE

    def extract(
        self, chunk: Chunk, doc: Document, normalizer: Normalizer, ctx: TraceContext
    ) -> list[ExtractedFact]:
        table = next((t for t in doc.tables if t.table_id == chunk.source_ref), None)
        if table is None or not table.header:
            return []
        header = [h.strip().casefold() for h in table.header]
        metric_cols = {i: _METRIC_COLUMNS[h] for i, h in enumerate(header) if h in _METRIC_COLUMNS}
        if not metric_cols:
            return []
        out: list[ExtractedFact] = []
        for r, row in enumerate(table.rows):
            if not row:
                continue
            subject_id = _ground(normalizer, row[0], ctx, table.caption or "")
            if not subject_id:
                continue
            qualifiers = _row_qualifiers(header, row)
            for col, (metric, unit) in metric_cols.items():
                if col >= len(row):
                    continue
                num = _NUM_RE.search(row[col])
                if not num:
                    continue
                out.append(
                    ExtractedFact(
                        fact_id="",
                        subject_id=subject_id,
                        predicate=PredicateEnum.in_clinical_trial_for,
                        object_value=num.group(),
                        object_unit=unit,
                        qualifiers=[f"metric={metric}", *qualifiers],
                        confidence=0.85,
                        extractor_id=self.extractor_id,
                        modality=self.modality,
                        evidence=[
                            Evidence(
                                chunk_id=chunk.chunk_id,
                                doc_id=chunk.doc_id,
                                section=chunk.section,
                                page=chunk.page,
                                bbox=tuple(chunk.bbox),
                                modality=self.modality,
                                quote=f"{table.header[col]}={row[col]} (row {r + 1}: {row[0]})",
                            )
                        ],
                    )
                )
        return out


def _row_qualifiers(header: list[str], row: list[str]) -> list[str]:
    """把非指标列变成 qualifier。没有 qualifier 的疗效数字是不可比的 —— 也就是无用的。"""
    keep = {"n", "population", "人群", "line", "治疗线", "arm", "组别", "dose", "剂量"}
    return [
        f"{h}={row[i].strip()}"
        for i, h in enumerate(header)
        if h in keep and i < len(row) and row[i].strip()
    ]


# ---------------------------------------------------------------- 图像通道


class ImageExtractor:
    """图像通道。PoC 消费视觉模型已输出的结构化字段，只做本体对齐与溯源挂接。

    KM 曲线读数的准确率由视觉模型决定，因此该通道 confidence 上限压得比表格低 ——
    抽样核验时也应按模态分层，否则文本通道的高准确率会掩盖图像通道的问题。
    """

    extractor_id = "image-vision-v1"
    modality = ModalityChannelEnum.IMAGE

    def extract(
        self, chunk: Chunk, doc: Document, normalizer: Normalizer, ctx: TraceContext
    ) -> list[ExtractedFact]:
        image = next((im for im in doc.images if im.image_id == chunk.source_ref), None)
        if image is None or not image.extracted_values:
            return []
        subject_id = _ground(normalizer, image.caption or "", ctx, doc.title)
        if not subject_id:
            return []
        out = []
        for metric, value in image.extracted_values.items():
            num = _NUM_RE.search(str(value))
            if not num:
                continue
            out.append(
                ExtractedFact(
                    fact_id="",
                    subject_id=subject_id,
                    predicate=PredicateEnum.in_clinical_trial_for,
                    object_value=num.group(),
                    object_unit=_unit_of(str(value)),
                    qualifiers=[f"metric={metric}", f"source_kind={image.kind}"],
                    confidence=0.6,
                    extractor_id=self.extractor_id,
                    modality=self.modality,
                    evidence=[
                        Evidence(
                            chunk_id=chunk.chunk_id,
                            doc_id=chunk.doc_id,
                            section=chunk.section,
                            page=chunk.page,
                            bbox=tuple(chunk.bbox),
                            modality=self.modality,
                            quote=f"{metric}={value} [{image.image_id}]",
                        )
                    ],
                )
            )
        return out


def _unit_of(value: str) -> str | None:
    m = re.search(r"[a-zA-Z%µ]+\s*$", value.strip())
    return m.group().strip() if m else None


# ---------------------------------------------------------------- 管线


class TriModalPipeline:
    """按 chunk 的模态路由到对应抽取器，并做跨模态事实合并。"""

    def __init__(self, extractors: list[FactExtractor] | None = None) -> None:
        self.extractors = extractors or [
            TextRelationExtractor(),
            TableExtractor(),
            ImageExtractor(),
        ]
        self._by_modality = {e.modality: e for e in self.extractors}

    def run(
        self,
        docs: list[Document],
        chunks: list[Chunk],
        *,
        normalizer: Normalizer,
        ctx: TraceContext,
    ) -> list[ExtractedFact]:
        doc_index = {d.doc_id: d for d in docs}
        raw: list[ExtractedFact] = []
        with ctx.span("extract", **{"hmd.chunk_count": len(chunks)}) as sp:
            for chunk in chunks:
                extractor = self._by_modality.get(chunk.modality)
                doc = doc_index.get(chunk.doc_id)
                if extractor is None or doc is None:
                    continue
                raw.extend(extractor.extract(chunk, doc, normalizer, ctx))
            merged = self.merge(raw)
            sp.set(**{"hmd.fact_count_raw": len(raw), "hmd.fact_count_merged": len(merged)})
        return merged

    def merge(self, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        """同一事实多处出现 → 合并证据并提升置信度。

        多证据支持是真实的可信度增量，但增益必须递减且封顶：
        同一篇综述里重复十遍不等于十份独立证据。
        """
        by_sig: dict[tuple, ExtractedFact] = {}
        for f in facts:
            sig = f.signature()
            cur = by_sig.get(sig)
            if cur is None:
                by_sig[sig] = f
                continue
            known = {(e.chunk_id, e.quote) for e in cur.evidence}
            for e in f.evidence:
                if (e.chunk_id, e.quote) not in known:
                    cur.evidence.append(e)
            distinct_docs = len({e.doc_id for e in cur.evidence})
            cur.confidence = min(
                0.97, max(cur.confidence, f.confidence) + 0.05 * (distinct_docs - 1)
            )
        out = sorted(by_sig.values(), key=lambda f: (f.subject_id, f.predicate.value, f.fact_id))
        for i, f in enumerate(out, start=1):
            f.fact_id = f"HMDF:{i:09d}"
        return out


def _ground(normalizer: Normalizer, text: str, ctx: TraceContext, context: str) -> str | None:
    """把抽取到的字符串挂到内部 CURIE。挂不上就丢弃 —— 未归一化的事实进不了图。"""
    text = text.strip(" \t\n,;:()[]")
    if not text:
        return None
    result = normalizer.normalize(text, ctx=ctx, context=context, min_confidence=0.6)
    if result.matched:
        return result.matched[0].concept_id
    result = normalizer.normalize(text, ctx=ctx, detect=True)
    return result.matched[0].concept_id if result.matched else None
