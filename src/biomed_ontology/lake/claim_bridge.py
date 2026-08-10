"""ExtractedFact / BERN2 提及 → KnowledgeClaim(extracted)。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from biomed_ontology.foundation.models import KnowledgeClaim

__all__ = ["CLAIM_PREDICATES", "evidence_id_for_chunk", "facts_to_claims", "mentions_to_entity_ids"]

CLAIM_PREDICATES = frozenset(
    {
        "targets",
        "investigates",
        "treats",
        "belongsTo",
        "testedIn",
        "hasAssay",
        "associatedWith",
        "mentions",
        "supportedBy",
        "sameAsExternal",
        "inhibits",
        "hasActivityIn",
        "hasMechanism",
        "inPathway",
        "hasBiomarker",
        "hasResult",
        "hasAdverseEvent",
    }
)

_PRED_ALIASES = {
    "has_target": "targets",
    "inhibits": "inhibits",
    "treats": "treats",
    "biomarker_for": "hasBiomarker",
    "in_clinical_trial_for": "investigates",
    "has_adverse_event": "hasAdverseEvent",
}


def evidence_id_for_chunk(chunk_id: str) -> str:
    return f"ev:chunk:{chunk_id}"


def mentions_to_entity_ids(
    mentions: list[Any],
    *,
    resolve_fn: Any,
) -> list[str]:
    """BERN2 mentions → HMD:ENT:*（unmapped 跳过）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in mentions:
        text = getattr(m, "mention", None) or str(m)
        ids = list(getattr(m, "ids", None) or [])
        ent = next((i for i in ids if str(i).startswith("HMD:ENT:")), None)
        if ent is None and resolve_fn is not None:
            ent = _canonical(resolve_fn(text))
        if ent and ent not in seen:
            seen.add(ent)
            out.append(ent)
    return out


def _canonical(hit_or_hits: Any) -> str | None:
    if hit_or_hits is None:
        return None
    if isinstance(hit_or_hits, dict):
        return hit_or_hits.get("canonical_entity")
    if isinstance(hit_or_hits, list):
        for h in hit_or_hits:
            c = _canonical(h)
            if c:
                return c
        return None
    return getattr(hit_or_hits, "canonical_entity", None)


def facts_to_claims(
    facts: list[Any],
    *,
    document_id: str,
    resolve_fn: Any = None,
) -> tuple[list[KnowledgeClaim], int]:
    """返回 (claims, skipped)。强制 claim_status=extracted。"""
    claims: list[KnowledgeClaim] = []
    skipped = 0
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for fact in facts:
        pred_raw = fact.predicate.value if hasattr(fact.predicate, "value") else str(fact.predicate)
        pred = _PRED_ALIASES.get(pred_raw, pred_raw)
        if pred not in CLAIM_PREDICATES:
            skipped += 1
            continue
        subj = _as_ent(fact.subject_id, resolve_fn)
        obj = _as_ent(fact.object_id, resolve_fn) if fact.object_id else None
        if not subj:
            skipped += 1
            continue
        evids = [evidence_id_for_chunk(e.chunk_id) for e in (fact.evidence or []) if e.chunk_id]
        digest = hashlib.sha1(f"{subj}|{pred}|{obj}|{document_id}".encode()).hexdigest()[:12]
        cid = f"claim:x:{digest}"
        claims.append(
            KnowledgeClaim(
                claim_id=cid,
                subject_id=subj,
                predicate=pred,
                object_id=obj,
                object_value=getattr(fact, "object_value", None),
                confidence=float(getattr(fact, "confidence", 0.5) or 0.5),
                claim_status="extracted",
                source_count=len(evids) or 1,
                source_id=document_id,
                source_type="literature",
                extracted_by=getattr(fact, "extractor_id", "tri_modal") or "tri_modal",
                evidence_ids=evids,
                span=(fact.evidence[0].quote if fact.evidence else None),
                created_at=now,
            )
        )
    return claims, skipped


def _as_ent(value: str | None, resolve_fn: Any) -> str | None:
    if not value:
        return None
    if value.startswith("HMD:ENT:"):
        return value
    if resolve_fn is None:
        return None
    return _canonical(resolve_fn(value))
