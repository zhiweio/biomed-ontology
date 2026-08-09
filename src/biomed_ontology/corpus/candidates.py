"""关系抽取候选：句内 mention 对 + 生物医药类型兼容矩阵。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from biomed_ontology._generated.hmd_concept import PredicateEnum

__all__ = [
    "COMPATIBLE_PREDICATES",
    "Mention",
    "MentionPair",
    "build_mention_pairs",
    "mentions_from_chunk",
    "split_sentences",
]

_SENT_SPLIT = re.compile(r"(?<=[.。!?？！])\s*")

# 粗类型：与 BERN2 / EntityType 对齐的小写标签
_TYPE_ALIASES = {
    "drug": "drug",
    "chemical": "drug",
    "compound": "drug",
    "substance": "drug",
    "gene": "target",
    "protein": "target",
    "target": "target",
    "disease": "disease",
    "indication": "disease",
    "mutation": "target",
    "adverse_event": "ae",
    "ae": "ae",
    # EntityTypeEnum.value
    "SUBSTANCE": "drug",
    "TARGET": "target",
    "DISEASE": "disease",
}

# (subject_type, object_type) → 允许的谓词
COMPATIBLE_PREDICATES: dict[tuple[str, str], frozenset[PredicateEnum]] = {
    ("drug", "target"): frozenset({PredicateEnum.inhibits, PredicateEnum.has_target}),
    ("drug", "disease"): frozenset(
        {PredicateEnum.treats, PredicateEnum.in_clinical_trial_for}
    ),
    ("target", "drug"): frozenset({PredicateEnum.biomarker_for}),
    ("drug", "ae"): frozenset({PredicateEnum.has_adverse_event}),
}


@dataclass(frozen=True)
class Mention:
    surface: str
    start: int
    end: int
    entity_type: str
    entity_id: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class MentionPair:
    sentence: str
    subject: Mention
    object: Mention
    allowed_predicates: frozenset[PredicateEnum]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]


def _norm_type(raw: str | None) -> str:
    if not raw:
        return ""
    key = str(raw).strip()
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    folded = key.casefold().replace(" ", "_")
    return _TYPE_ALIASES.get(folded, folded)


def mentions_from_chunk(
    chunk: Any,
    *,
    normalizer: Any | None = None,
    ctx: Any | None = None,
) -> list[Mention]:
    """优先 entity_ids（已解析 ENT）；否则 Normalizer detect。"""
    text = str(getattr(chunk, "text", "") or "")
    out: list[Mention] = []
    ents = list(getattr(chunk, "entity_ids", None) or [])
    if ents and normalizer is not None:
        for eid in ents:
            concept = normalizer.concept(eid) if hasattr(normalizer, "concept") else None
            label = ""
            etype = ""
            if concept is not None:
                label = (
                    getattr(concept, "preferred_label_en", None)
                    or getattr(concept, "seed_key", None)
                    or eid
                )
                et = getattr(concept, "entity_type", None)
                etype = _norm_type(et.value if hasattr(et, "value") else str(et or ""))
            else:
                label = eid
            idx = text.casefold().find(str(label).casefold()) if label else -1
            start = idx if idx >= 0 else 0
            end = start + len(label) if idx >= 0 else 0
            out.append(
                Mention(
                    surface=str(label),
                    start=start,
                    end=end,
                    entity_type=etype or "drug",
                    entity_id=str(eid),
                    confidence=0.9,
                )
            )
        if out:
            return out

    if normalizer is None or not text.strip():
        return out
    result = normalizer.normalize(text, ctx=ctx, detect=True, min_confidence=0.6)
    for m in getattr(result, "matched", ()) or ():
        surface = getattr(m, "matched_text", None) or getattr(m, "span", None) or ""
        surface = str(surface)
        cid = getattr(m, "concept_id", None)
        et = getattr(m, "entity_type", None)
        etype = _norm_type(et.value if hasattr(et, "value") else str(et or ""))
        start = int(getattr(m, "char_start", 0) or 0)
        end = int(getattr(m, "char_end", start + len(surface)) or start + len(surface))
        out.append(
            Mention(
                surface=surface or str(cid or ""),
                start=start,
                end=end,
                entity_type=etype or "",
                entity_id=str(cid) if cid else None,
                confidence=float(getattr(m, "confidence", 0.7) or 0.7),
            )
        )
    return out


def build_mention_pairs(
    text: str,
    mentions: Iterable[Mention],
    *,
    max_pairs: int = 32,
    cross_sentence: bool = False,
) -> list[MentionPair]:
    """同句实体对；按置信度 × 类型兼容排序，截断到 max_pairs。"""
    mentions = list(mentions)
    sentences = split_sentences(text)
    if not sentences or len(mentions) < 2:
        return []

    # 将 mention 映射到句索引（按 start 偏移近似）
    offsets: list[int] = []
    cursor = 0
    full = text
    for sent in sentences:
        idx = full.find(sent, cursor)
        if idx < 0:
            idx = cursor
        offsets.append(idx)
        cursor = idx + len(sent)

    def sent_idx(m: Mention) -> int:
        for i, off in enumerate(offsets):
            end = off + len(sentences[i])
            if off <= m.start < end or (m.start == 0 and m.surface.casefold() in sentences[i].casefold()):
                return i
        # surface 回落
        for i, sent in enumerate(sentences):
            if m.surface and m.surface.casefold() in sent.casefold():
                return i
        return 0

    scored: list[tuple[float, MentionPair]] = []
    for i, a in enumerate(mentions):
        for b in mentions[i + 1 :]:
            sa, sb = sent_idx(a), sent_idx(b)
            if sa != sb and not cross_sentence:
                continue
            if abs(sa - sb) > 1:
                continue
            sent = sentences[min(sa, sb)]
            for subj, obj in ((a, b), (b, a)):
                st, ot = _norm_type(subj.entity_type), _norm_type(obj.entity_type)
                allowed = COMPATIBLE_PREDICATES.get((st, ot))
                if not allowed:
                    continue
                score = subj.confidence + obj.confidence + (0.2 if sa == sb else 0.0)
                scored.append(
                    (
                        score,
                        MentionPair(
                            sentence=sent,
                            subject=subj,
                            object=obj,
                            allowed_predicates=allowed,
                        ),
                    )
                )
    scored.sort(key=lambda x: -x[0])
    # 去重同一 (s_id/surface, o_id/surface, sent)
    seen: set[tuple[str, str, str]] = set()
    out: list[MentionPair] = []
    for _, pair in scored:
        key = (
            pair.subject.entity_id or pair.subject.surface,
            pair.object.entity_id or pair.object.surface,
            pair.sentence[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(pair)
        if len(out) >= max_pairs:
            break
    return out
