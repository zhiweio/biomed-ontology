"""Stage B 受限 LLM 裁决：disposition only，不选 op/target，不推翻硬 dismiss。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from biomed_ontology.foundation.paths import REPO_ROOT
from biomed_ontology.llm.chat import ChatProvider, ChatResult, NullChatProvider, get_chat_provider

__all__ = [
    "ALLOWED_LABELS",
    "DISPOSITIONS",
    "HARD_REASONS",
    "HARD_REASON_PREFIXES",
    "LlmFilterStats",
    "adjudicate_candidates",
    "is_hard_dismiss",
    "parse_adjudication_payload",
    "select_llm_pool",
    "validate_item",
]

DISPOSITIONS = frozenset({"keep", "dismiss", "soft_downrank"})
ALLOWED_LABELS = frozenset(
    {
        "biomedical_alias",
        "noise",
        "test_traffic",
        "fragment",
        "ambiguous",
        "non_entity",
    }
)
HARD_REASONS = frozenset(
    {
        "too_short",
        "too_long",
        "allow_miss",
        "gold_expect_null",
        "batch_skipped",
        "dedupe",
    }
)
HARD_REASON_PREFIXES = ("deny_pattern:", "drop_method:")

DEFAULT_PROMPT_PATH = REPO_ROOT / "ontology" / "policies" / "evolve_llm_filter_prompt.md"

_DEFAULT_SYSTEM = """You are a biomedical ontology evolution filter adjudicator.
Decide keep / dismiss / soft_downrank for entity-resolution mention candidates.
Output JSON only: {"items":[{"mention_key","disposition","labels","confidence","rationale"}]}.
Rules:
- disposition must be one of: keep, dismiss, soft_downrank
- labels from: biomedical_alias, noise, test_traffic, fragment, ambiguous, non_entity
- Do NOT invent enterprise IDs, CURIEs, or create-node suggestions
- dismiss only for clear noise / test traffic / non-entity / BERN2 fragments
- keep real drug/gene/disease aliases and abbreviations even if short
- rationale <= 200 characters
- mention_key must exactly match the input mention_key
"""


@dataclass
class LlmFilterStats:
    judged: int = 0
    dismissed: int = 0
    kept: int = 0
    soft_downrank: int = 0
    fallback: int = 0
    batches: int = 0
    skipped_hard: int = 0
    provider: str = "null"
    calls: int = 0

    def as_dict(self) -> dict[str, int | str]:
        return {
            "llm_judged": self.judged,
            "llm_dismissed": self.dismissed,
            "llm_kept": self.kept,
            "llm_soft_downrank": self.soft_downrank,
            "llm_fallback": self.fallback,
            "llm_batches": self.batches,
            "llm_skipped_hard": self.skipped_hard,
            "llm_provider": self.provider,
            "llm_calls": self.calls,
        }


def is_hard_dismiss(reasons: list[str] | None) -> bool:
    return any(r in HARD_REASONS or r.startswith(HARD_REASON_PREFIXES) for r in reasons or [])


def select_llm_pool(
    keep: list[dict[str, Any]],
    borderline: list[dict[str, Any]],
    *,
    route: str = "borderline",
) -> list[dict[str, Any]]:
    """Pick candidates eligible for LLM (never hard-dismissed)."""
    pool: list[dict[str, Any]] = []
    for row in borderline:
        if is_hard_dismiss(list(row.get("filter_reasons") or [])):
            continue
        pool.append(row)
    if route == "all_keep":
        pool.extend(keep)
    else:
        # borderline route: also judge unclear keeps
        for row in keep:
            overlap = float(row.get("query_overlap") or 0)
            method = str(row.get("resolution_method") or "")
            conf = float(row.get("confidence") or 0)
            clear = overlap >= 1.0 and (
                method in {"dictionary", "zingg", "xref", "enterprise_id"} or conf >= 0.9
            )
            if not clear:
                pool.append(row)
    # dedupe by mention_key preserving order
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in pool:
        key = str(row.get("mention_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _load_system_prompt(path: Path | None) -> str:
    p = path or DEFAULT_PROMPT_PATH
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return _DEFAULT_SYSTEM


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def validate_item(
    raw: dict[str, Any],
    *,
    expected_key: str,
    min_confidence: float,
    allow_dismiss_labels: set[str],
    max_rationale_chars: int,
) -> dict[str, Any] | None:
    """Return normalized item or None if invalid / must ignore."""
    key = str(raw.get("mention_key") or "").strip()
    if key != expected_key:
        return None
    disposition = str(raw.get("disposition") or "").strip().lower()
    if disposition not in DISPOSITIONS:
        return None
    raw_conf = raw.get("confidence")
    if raw_conf is None:
        return None
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        return None
    if conf < 0 or conf > 1:
        return None
    labels = [str(x) for x in (raw.get("labels") or []) if str(x) in ALLOWED_LABELS]
    rationale = str(raw.get("rationale") or "")[:max_rationale_chars]
    if disposition == "dismiss" and (
        conf < min_confidence or not (set(labels) & allow_dismiss_labels)
    ):
        disposition = "soft_downrank"
    return {
        "mention_key": key,
        "disposition": disposition,
        "labels": labels,
        "confidence": conf,
        "rationale": rationale,
    }


def parse_adjudication_payload(
    text: str,
    *,
    expected_keys: list[str],
    min_confidence: float = 0.6,
    allow_dismiss_labels: set[str] | None = None,
    max_rationale_chars: int = 200,
) -> dict[str, dict[str, Any]]:
    """Parse batch JSON → mention_key → validated item."""
    allow = allow_dismiss_labels or {
        "noise",
        "test_traffic",
        "fragment",
        "non_entity",
    }
    data = _extract_json(text)
    items = data.get("items")
    if not isinstance(items, list):
        # single-object fallback
        if isinstance(data.get("mention_key"), str):
            items = [data]
        else:
            return {}
    by_key = {str(k): True for k in expected_keys}
    out: dict[str, dict[str, Any]] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("mention_key") or "")
        if key not in by_key:
            continue
        validated = validate_item(
            raw,
            expected_key=key,
            min_confidence=min_confidence,
            allow_dismiss_labels=allow,
            max_rationale_chars=max_rationale_chars,
        )
        if validated:
            out[key] = validated
    return out


def _batch_user_payload(cands: list[dict[str, Any]]) -> str:
    rows = []
    for c in cands:
        rows.append(
            {
                "mention_key": c.get("mention_key"),
                "mention": c.get("mention"),
                "query": c.get("query"),
                "resolution_method": c.get("resolution_method"),
                "confidence": c.get("confidence"),
                "query_overlap": c.get("query_overlap"),
                "occurrences": c.get("occurrences"),
                "external_ids": [
                    x
                    for x in (c.get("external_ids") or [])
                    if x and str(x).upper() not in {"CUI-LESS", "CUILESS"}
                ][:5],
                "filter_reasons": c.get("filter_reasons") or [],
            }
        )
    return (
        "Adjudicate each candidate. Return JSON "
        '{"items":[{"mention_key","disposition","labels","confidence","rationale"}]}.\n'
        f"Candidates: {json.dumps(rows, ensure_ascii=False)}"
    )


def adjudicate_candidates(
    keep: list[dict[str, Any]],
    dismissed: list[dict[str, Any]],
    borderline: list[dict[str, Any]],
    *,
    llm_policy: dict[str, Any] | None = None,
    chat: ChatProvider | None = None,
    prompt_path: Path | None = None,
    progress: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], LlmFilterStats]:
    """Apply constrained LLM on pool; hard dismiss stays dismissed."""
    cfg = llm_policy or {}
    stats = LlmFilterStats()
    if not cfg.get("enabled", True):
        return keep, dismissed, borderline, stats

    provider = chat or get_chat_provider()
    stats.provider = getattr(provider, "name", "unknown")
    if isinstance(provider, NullChatProvider) or stats.provider == "null":
        stats.fallback = len(borderline)
        return keep, dismissed, borderline, stats

    route = str(cfg.get("route") or "borderline")
    batch_size = max(1, int(cfg.get("batch_size") or 16))
    min_conf = float(cfg.get("min_confidence") or 0.6)
    max_rat = int(cfg.get("max_rationale_chars") or 200)
    allow_dismiss = {
        str(x)
        for x in (
            cfg.get("allow_dismiss_labels")
            or [
                "noise",
                "test_traffic",
                "fragment",
                "non_entity",
            ]
        )
    }
    system = _load_system_prompt(prompt_path)

    # Split dismissed into hard vs previously soft (should already be in borderline)
    hard_dismissed = [d for d in dismissed if is_hard_dismiss(list(d.get("filter_reasons") or []))]
    stats.skipped_hard = len(hard_dismissed)

    pool = select_llm_pool(keep, borderline, route=route)
    if not pool:
        return (
            keep,
            hard_dismissed
            + [d for d in dismissed if not is_hard_dismiss(list(d.get("filter_reasons") or []))],
            borderline,
            stats,
        )

    keep_by_key = {str(k.get("mention_key")): k for k in keep}
    border_by_key = {str(k.get("mention_key")): k for k in borderline}
    all_by_key = {**border_by_key, **keep_by_key}

    decisions: dict[str, dict[str, Any]] = {}
    batches = [pool[i : i + batch_size] for i in range(0, len(pool), batch_size)]
    if progress is not None:
        progress.total = len(batches)

    for batch in batches:
        keys = [str(c["mention_key"]) for c in batch]
        try:
            result: ChatResult = provider.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _batch_user_payload(batch)},
                ],
                response_format={"type": "json_object"},
            )
            stats.calls += 1
            parsed = parse_adjudication_payload(
                result.text,
                expected_keys=keys,
                min_confidence=min_conf,
                allow_dismiss_labels=allow_dismiss,
                max_rationale_chars=max_rat,
            )
            for key in keys:
                if key in parsed:
                    decisions[key] = parsed[key]
                else:
                    stats.fallback += 1
        except Exception:
            stats.fallback += len(keys)
        stats.batches += 1
        if progress is not None:
            progress.update(1)

    new_keep: list[dict[str, Any]] = []
    new_dismissed: list[dict[str, Any]] = list(hard_dismissed)
    new_soft: list[dict[str, Any]] = []
    judged_keys = set(decisions)

    # Start from original keep not in pool decisions
    for row in keep:
        key = str(row.get("mention_key"))
        if key not in judged_keys:
            new_keep.append(row)

    for key, decision in decisions.items():
        base = dict(all_by_key.get(key) or {})
        if not base:
            continue
        stats.judged += 1
        base["llm_disposition"] = decision["disposition"]
        base["llm_labels"] = decision["labels"]
        base["llm_confidence"] = decision["confidence"]
        base["llm_rationale"] = decision["rationale"]
        disp = decision["disposition"]
        if disp == "dismiss":
            base["risk_tier"] = "L0"
            base.setdefault("filter_reasons", [])
            reasons = list(base.get("filter_reasons") or [])
            reasons.append("llm_dismiss")
            base["filter_reasons"] = reasons
            new_dismissed.append(base)
            stats.dismissed += 1
        elif disp == "soft_downrank":
            base["risk_tier"] = "L0"
            # still enrich (downranked) per plan
            base["rank_score"] = float(base.get("rank_score") or 0) * 0.5
            new_keep.append(base)
            new_soft.append(base)
            stats.soft_downrank += 1
        else:
            # keep — rescue from borderline
            reasons = [
                r
                for r in (base.get("filter_reasons") or [])
                if r
                not in {
                    "low_query_overlap",
                    "below_min_occurrences",
                    "below_method_confidence",
                }
            ]
            base["filter_reasons"] = reasons
            if "rank_score" not in base:
                base["rank_score"] = 1.0
            new_keep.append(base)
            stats.kept += 1

    # Borderline not judged → keep rule decision (stay dismissed / soft)
    for row in borderline:
        key = str(row.get("mention_key"))
        if key in judged_keys:
            continue
        new_dismissed.append(row)
        new_soft.append(row)
        stats.fallback += 1

    new_keep.sort(key=lambda r: (-float(r.get("rank_score") or 0), r.get("mention_key") or ""))
    return new_keep, new_dismissed, new_soft, stats
