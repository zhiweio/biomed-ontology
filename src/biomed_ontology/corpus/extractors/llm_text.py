"""受限 JSON 文本关系抽取（text-llm-v1）。"""

from __future__ import annotations

import json
from typing import Any

from biomed_ontology._generated.hmd_concept import PredicateEnum, ReviewStatusEnum
from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
from biomed_ontology.corpus import Chunk, Document
from biomed_ontology.corpus.candidates import (
    build_mention_pairs,
    mentions_from_chunk,
)
from biomed_ontology.corpus.extract import Evidence, ExtractedFact, _ground
from biomed_ontology.llm.chat import ChatProvider, NullChatProvider, get_chat_provider
from biomed_ontology.normalize import Normalizer
from biomed_ontology.observability import TraceContext

__all__ = ["LlmTextRelationExtractor", "system_prompt_for"]

_ALLOWED = frozenset(
    {
        "inhibits",
        "treats",
        "has_target",
        "biomarker_for",
        "has_adverse_event",
        "in_clinical_trial_for",
        "none",
    }
)

_SYSTEM = (
    "You are a biomedical relation extractor. "
    "Given a sentence and candidate entities, return JSON "
    '{"relations":[{"subject","object","predicate","negated","uncertain","confidence","quote"}]}. '
    "predicate MUST be one of: inhibits, treats, has_target, biomarker_for, "
    "has_adverse_event, in_clinical_trial_for, none. "
    "Cover drugs, targets, diseases, genes, and adverse events when present. "
    "Use none when no clear relation. Set negated=true for negation. "
    "quote must be a verbatim substring of the sentence. "
    "Do not invent entity IDs; use the provided surfaces or IDs only."
)

_SYSTEM_BY_DOCTYPE: dict[str, str] = {
    "JOURNAL_ARTICLE": (
        _SYSTEM + " Domain=literature. Prefer mechanism (inhibits/has_target), "
        "biomarker_for, and gene–target mentions. Do not mint new IDs."
    ),
    "CLINICAL_STUDY_REPORT": (
        _SYSTEM + " Domain=CSR. Prefer in_clinical_trial_for, treats, has_adverse_event. "
        "Keep endpoints and AE terms grounded to provided entities only."
    ),
    "INVESTIGATOR_BROCHURE": (
        _SYSTEM + " Domain=IB. Prefer has_target, treats, has_adverse_event, "
        "in_clinical_trial_for. Safety and mechanism only from the sentence."
    ),
    "LABEL": (
        _SYSTEM + " Domain=label/SmPC. Prefer treats, has_adverse_event, has_target. "
        "Do not infer off-label uses."
    ),
    "PATENT": (
        _SYSTEM + " Domain=patent. Prefer has_target and inhibits. "
        "Ignore claim-construction rhetoric; extract only explicit relations."
    ),
}


def system_prompt_for(doc: Document | None) -> str:
    raw = getattr(getattr(doc, "doc_type", None), "value", None) or str(
        getattr(doc, "doc_type", "") or ""
    )
    return _SYSTEM_BY_DOCTYPE.get(raw, _SYSTEM)


class LlmTextRelationExtractor:
    """schema-guided LLM RE：候选对 + 受限 JSON → ExtractedFact。"""

    extractor_id = "text-llm-v1"
    modality = ModalityChannelEnum.TEXT

    def __init__(
        self,
        chat: ChatProvider | None = None,
        *,
        min_confidence: float = 0.55,
        max_confidence: float = 0.85,
        max_pairs: int = 32,
        enabled: bool = True,
    ) -> None:
        self.chat = chat
        self.min_confidence = min_confidence
        self.max_confidence = max_confidence
        self.max_pairs = max_pairs
        self.enabled = enabled

    def extract(
        self, chunk: Chunk, doc: Document, normalizer: Normalizer, ctx: TraceContext
    ) -> list[ExtractedFact]:
        if not self.enabled:
            return []
        chat = self.chat or get_chat_provider()
        inner = getattr(chat, "provider", chat)
        if isinstance(inner, NullChatProvider):
            # 无 API key / provider=null：不调用（由规则旁路覆盖离线路径）
            return []

        mentions = mentions_from_chunk(chunk, normalizer=normalizer, ctx=ctx)
        pairs = build_mention_pairs(chunk.text, mentions, max_pairs=self.max_pairs)
        if not pairs:
            return []

        # 按句聚合，减少调用
        by_sent: dict[str, list[Any]] = {}
        for p in pairs:
            by_sent.setdefault(p.sentence, []).append(p)

        out: list[ExtractedFact] = []
        for sentence, sent_pairs in by_sent.items():
            entities = []
            seen: set[str] = set()
            for p in sent_pairs:
                for m in (p.subject, p.object):
                    key = m.entity_id or m.surface
                    if key in seen:
                        continue
                    seen.add(key)
                    entities.append(
                        {
                            "surface": m.surface,
                            "type": m.entity_type,
                            "id": m.entity_id,
                        }
                    )
            allowed = sorted({pred.value for p in sent_pairs for pred in p.allowed_predicates})
            user = (
                f"Sentence: {sentence}\n"
                f"Entities: {json.dumps(entities, ensure_ascii=False)}\n"
                f"Allowed predicates for these types: {allowed}\n"
                "Return JSON only."
            )
            result = chat.complete(
                [
                    {"role": "system", "content": system_prompt_for(doc)},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
            out.extend(
                self._parse_relations(
                    result.text,
                    chunk=chunk,
                    sentence=sentence,
                    normalizer=normalizer,
                    ctx=ctx,
                    sent_pairs=sent_pairs,
                )
            )
        return out

    def _parse_relations(
        self,
        payload: str,
        *,
        chunk: Chunk,
        sentence: str,
        normalizer: Normalizer,
        ctx: TraceContext,
        sent_pairs: list[Any],
    ) -> list[ExtractedFact]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        rows = data.get("relations") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []

        id_by_surface: dict[str, str] = {}
        for p in sent_pairs:
            for m in (p.subject, p.object):
                if m.entity_id:
                    id_by_surface[m.surface.casefold()] = m.entity_id
                    id_by_surface[m.entity_id.casefold()] = m.entity_id

        out: list[ExtractedFact] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("negated") or row.get("uncertain"):
                continue
            pred_raw = str(row.get("predicate") or "none").strip()
            if pred_raw not in _ALLOWED or pred_raw == "none":
                continue
            try:
                predicate = PredicateEnum(pred_raw)
            except ValueError:
                continue
            conf = float(row.get("confidence") or 0.6)
            conf = min(self.max_confidence, max(0.0, conf))
            if conf < self.min_confidence:
                continue

            s_raw = str(row.get("subject") or "").strip()
            o_raw = str(row.get("object") or "").strip()
            s_id = id_by_surface.get(s_raw.casefold()) or _ground(normalizer, s_raw, ctx, sentence)
            o_id = id_by_surface.get(o_raw.casefold()) or _ground(normalizer, o_raw, ctx, sentence)
            if not s_id or not o_id or s_id == o_id:
                continue
            quote = str(row.get("quote") or sentence).strip()[:300]
            if quote and quote not in sentence and quote not in chunk.text:
                quote = sentence[:300]
            out.append(
                ExtractedFact(
                    fact_id="",
                    subject_id=s_id,
                    predicate=predicate,
                    object_id=o_id,
                    confidence=conf,
                    extractor_id=self.extractor_id,
                    modality=self.modality,
                    review_status=ReviewStatusEnum.PENDING,
                    evidence=[
                        Evidence(
                            chunk_id=chunk.chunk_id,
                            doc_id=chunk.doc_id,
                            section=chunk.section,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            page=chunk.page,
                            modality=self.modality,
                            quote=quote,
                        )
                    ],
                )
            )
        return out
