"""受限 LLM filter 裁决：假 ChatProvider，不打外网。"""

from __future__ import annotations

import json
from pathlib import Path

from biomed_ontology.foundation.evolve_llm_filter import (
    adjudicate_candidates,
    is_hard_dismiss,
    parse_adjudication_payload,
    select_llm_pool,
    validate_item,
)
from biomed_ontology.foundation.evolve_propose import (
    filter_candidates,
    load_candidates_files,
    load_filter_policy,
    load_gold_null_keys,
    run_enrich,
)
from biomed_ontology.llm.chat import ChatResult, NullChatProvider

FIXTURE = Path(__file__).parent / "fixtures" / "evolve" / "sample.candidates.json"


class FakeChat:
    name = "fake"

    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.calls = 0
        self.messages_seen: list = []

    def complete(self, messages, *, response_format=None) -> ChatResult:
        self.calls += 1
        self.messages_seen.append(messages)
        text = (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload, ensure_ascii=False)
        )
        return ChatResult(text=text)


def test_hard_dismiss_never_enters_llm_pool() -> None:
    policy = load_filter_policy()
    cands, _ = load_candidates_files([FIXTURE])
    gold = load_gold_null_keys(policy.gold_resolve_path)
    keep, dismissed, soft = filter_candidates(cands, policy, gold_null_keys=gold)
    hard = [d for d in dismissed if is_hard_dismiss(list(d.get("filter_reasons") or []))]
    assert hard
    assert any("deny_pattern" in str(d.get("filter_reasons")) for d in hard)
    pool = select_llm_pool(keep, soft, route="borderline")
    hard_keys = {d["mention_key"] for d in hard}
    assert hard_keys.isdisjoint({p["mention_key"] for p in pool})


def test_validate_item_rejects_key_tamper_and_low_conf_dismiss() -> None:
    assert (
        validate_item(
            {
                "mention_key": "other",
                "disposition": "dismiss",
                "labels": ["noise"],
                "confidence": 0.9,
            },
            expected_key="xyz",
            min_confidence=0.6,
            allow_dismiss_labels={"noise"},
            max_rationale_chars=200,
        )
        is None
    )
    downgraded = validate_item(
        {
            "mention_key": "xyz",
            "disposition": "dismiss",
            "labels": ["noise"],
            "confidence": 0.2,
        },
        expected_key="xyz",
        min_confidence=0.6,
        allow_dismiss_labels={"noise"},
        max_rationale_chars=200,
    )
    assert downgraded is not None
    assert downgraded["disposition"] == "soft_downrank"


def test_parse_invalid_json_returns_empty() -> None:
    assert parse_adjudication_payload("not-json", expected_keys=["a"]) == {}


def test_adjudicate_dismisses_noise_and_keeps_alias() -> None:
    keep = [
        {
            "mention": "er-alias-for-sclc",
            "mention_key": "er-alias-for-sclc",
            "query": "er-alias-for-sclc",
            "query_overlap": 1.0,
            "confidence": 0.9,
            "resolution_method": "bern2_candidate",
            "external_ids": ["HMD:ENT:IND:sclc"],
            "rank_score": 2.0,
            "filter_reasons": [],
        }
    ]
    soft = [
        {
            "mention": "xyz",
            "mention_key": "xyz",
            "query": "unknownzyme-xyz-999",
            "query_overlap": 0.33,
            "confidence": 0.5,
            "resolution_method": "bern2_candidate",
            "filter_reasons": ["low_query_overlap"],
            "borderline": True,
        }
    ]
    hard = [
        {
            "mention": "e2e-obs-12345",
            "mention_key": "e2e-obs-12345",
            "filter_reasons": ["deny_pattern:(?i)^e2e[-_]"],
            "risk_tier": "L0",
        }
    ]
    fake = FakeChat(
        {
            "items": [
                {
                    "mention_key": "xyz",
                    "disposition": "dismiss",
                    "labels": ["fragment", "noise"],
                    "confidence": 0.91,
                    "rationale": "BERN2 fragment",
                },
                {
                    "mention_key": "er-alias-for-sclc",
                    "disposition": "keep",
                    "labels": ["biomedical_alias"],
                    "confidence": 0.88,
                    "rationale": "disease alias",
                },
            ]
        }
    )
    new_keep, new_dismissed, _, stats = adjudicate_candidates(
        keep,
        hard + soft,
        soft,
        llm_policy={
            "enabled": True,
            "route": "all_keep",
            "batch_size": 8,
            "min_confidence": 0.6,
            "allow_dismiss_labels": ["noise", "fragment", "test_traffic", "non_entity"],
        },
        chat=fake,
    )
    assert fake.calls == 1
    assert stats.judged == 2
    assert stats.dismissed == 1
    assert any(d["mention_key"] == "xyz" for d in new_dismissed)
    assert any(k["mention_key"] == "er-alias-for-sclc" for k in new_keep)
    # hard deny never judged
    assert any(d["mention_key"] == "e2e-obs-12345" for d in new_dismissed)
    assert "e2e-obs-12345" not in json.dumps(
        fake.messages_seen[0][1]["content"], ensure_ascii=False
    )


def test_null_provider_keeps_rule_decision() -> None:
    keep = [{"mention_key": "a", "mention": "a", "rank_score": 1, "filter_reasons": []}]
    soft = [
        {
            "mention_key": "b",
            "mention": "b",
            "filter_reasons": ["low_query_overlap"],
            "borderline": True,
        }
    ]
    new_keep, new_dismissed, _new_soft, stats = adjudicate_candidates(
        keep,
        soft,
        soft,
        llm_policy={"enabled": True},
        chat=NullChatProvider(),
    )
    assert new_keep == keep
    assert stats.provider == "null"
    assert stats.fallback >= 1
    assert any(d["mention_key"] == "b" for d in new_dismissed)


def test_run_enrich_with_fake_llm(tmp_path: Path) -> None:
    fake = FakeChat(
        {
            "items": [
                {
                    "mention_key": "xyz",
                    "disposition": "dismiss",
                    "labels": ["fragment"],
                    "confidence": 0.95,
                    "rationale": "fragment",
                },
                {
                    "mention_key": "er-alias-for-sclc",
                    "disposition": "keep",
                    "labels": ["biomedical_alias"],
                    "confidence": 0.9,
                    "rationale": "ok",
                },
                {
                    "mention_key": "tiny-cell lung ca",
                    "disposition": "keep",
                    "labels": ["biomedical_alias"],
                    "confidence": 0.85,
                    "rationale": "ok",
                },
            ]
        }
    )
    result = run_enrich(
        from_paths=[FIXTURE],
        out_dir=tmp_path,
        skip_tools=True,
        use_llm=True,
        chat=fake,
    )
    assert result.counts.get("llm_judged", 0) >= 1
    assert fake.calls >= 1
    mentions = {p["mention"] for p in result.proposals}
    assert "er-alias-for-sclc" in mentions
    # hard deny patterns never proposed
    assert "e2e-obs-12345" not in mentions
    assert "CHEBI:DEMO_ASPIRIN" not in mentions


def test_run_enrich_no_llm_flag(tmp_path: Path) -> None:
    fake = FakeChat({"items": []})
    result = run_enrich(
        from_paths=[FIXTURE],
        out_dir=tmp_path,
        skip_tools=True,
        use_llm=False,
        chat=fake,
    )
    assert fake.calls == 0
    assert result.counts.get("llm_judged", 0) == 0
