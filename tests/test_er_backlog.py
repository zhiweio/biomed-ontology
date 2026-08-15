"""ER backlog：去重、最新状态、overlay、emit=False、闭环 mapped。"""

from __future__ import annotations

from pathlib import Path

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.er_backlog import (
    aggregate_er_records,
    emit_mapped_mentions,
    open_er_mentions,
)
from biomed_ontology.foundation.world import load_world_model
from biomed_ontology.pipelines.ops import evaluate_slo


def _row(
    *,
    oid: str,
    mention: str,
    status: str,
    ts: str,
    date: str | None = None,
    source: str = "runtime_resolve",
) -> dict:
    return {
        "observation_id": oid,
        "mention": mention,
        "mention_key": mention.lower(),
        "resolve_status": status,
        "event_ts": ts,
        "event_date": date or ts[:10],
        "source": source,
    }


def test_aggregate_dedupes_observation_id() -> None:
    records = [
        _row(oid="a", mention="foo", status="unmapped", ts="2026-01-01T00:00:00Z"),
        _row(oid="a", mention="foo", status="unmapped", ts="2026-01-01T00:00:00Z"),
        _row(oid="b", mention="foo", status="unmapped", ts="2026-01-02T00:00:00Z"),
    ]
    result = aggregate_er_records(records)
    assert result.raw_rows == 3
    assert result.unique_events == 2
    assert len(result.rows) == 1
    assert result.rows[0]["occurrences"] == 2
    assert result.rows[0]["latest_status"] == "unmapped"


def test_aggregate_mapped_latest_closes() -> None:
    records = [
        _row(oid="a", mention="foo", status="unmapped", ts="2026-01-01T00:00:00Z"),
        _row(oid="b", mention="foo", status="mapped", ts="2026-01-02T00:00:00Z"),
    ]
    result = aggregate_er_records(records)
    assert result.rows == []
    assert result.unique_events == 2


def test_aggregate_dismissed_closes() -> None:
    records = [
        _row(oid="a", mention="bar", status="unmapped", ts="2026-01-01T00:00:00Z"),
        _row(oid="b", mention="bar", status="dismissed", ts="2026-01-03T00:00:00Z"),
    ]
    assert aggregate_er_records(records).rows == []


def test_open_er_mentions_overlay_drops_dictionary_hits() -> None:
    rows = [
        {"label": "HMPL-504", "occurrences": 3, "mention_key": "hmpl-504"},
        {"label": "unknownzyme-xyz-999", "occurrences": 1, "mention_key": "unknownzyme-xyz-999"},
    ]
    opened = open_er_mentions(rows)
    labels = {r["label"] for r in opened}
    assert "HMPL-504" not in labels
    assert "unknownzyme-xyz-999" in labels


def test_resolve_entity_emit_false_does_not_produce(monkeypatch) -> None:
    called: list[dict] = []

    def _capture(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr(
        "biomed_ontology.lake.obs_events.emit_er_observation",
        _capture,
    )
    api = FoundationApi(load_world_model())
    api.resolve_entity("unknownzyme-xyz-999", emit=False)
    assert called == []
    api.resolve_entity("unknownzyme-xyz-999", emit=True)
    assert called
    assert called[0]["resolve_status"] == "unmapped"


def test_emit_mapped_mentions_calls_producer(monkeypatch) -> None:
    called: list[dict] = []
    monkeypatch.setattr(
        "biomed_ontology.lake.obs_events.emit_er_observation",
        lambda **kwargs: called.append(kwargs),
    )
    n = emit_mapped_mentions(["savolitinb", ""], source="evolve_mine", tool_name="evolve-mine")
    assert n == 1
    assert called[0]["resolve_status"] == "mapped"
    assert called[0]["mention"] == "savolitinb"


def test_evaluate_slo_unique_backlog_and_mine_stale() -> None:
    policy = {
        "er_observations": {
            "unmapped_backlog_max": 5,
            "require_nightly_mine_if_over": True,
            "mine_max_age_hours": 24,
        }
    }
    base = {
        "open_quarantine_n": 0,
        "open_quarantine_oldest_age_h": 0,
        "world_model_fingerprint_age_h": 1,
        "release_scorecard_age_h": 1,
        "er_unmapped_backlog": 10,
        "obs_wal_lines": 0,
        "connect_ok": True,
        "env": "dev",
    }
    stale = evaluate_slo({**base, "er_mine_age_h": 48}, policy=policy)
    assert stale["ok"] is False
    assert any("backlog 10 over SLO" in r for r in stale["red"])
    assert any("mine stale" in r for r in stale["red"])

    fresh = evaluate_slo({**base, "er_mine_age_h": 1}, policy=policy)
    assert fresh["ok"] is False
    assert any("backlog 10 over SLO" in r for r in fresh["red"])
    assert not any("mine stale" in r for r in fresh["red"])

    under = evaluate_slo({**base, "er_unmapped_backlog": 2, "er_mine_age_h": 48}, policy=policy)
    assert under["ok"] is True


def test_apply_approved_emits_mapped(tmp_path: Path, monkeypatch) -> None:
    from biomed_ontology.foundation.evolve_apply import apply_approved, save_proposals

    called: list[dict] = []
    monkeypatch.setattr(
        "biomed_ontology.lake.obs_events.emit_er_observation",
        lambda **kwargs: called.append(kwargs),
    )
    dict_path = tmp_path / "dict.yaml"
    dict_path.write_text(
        (
            'version: "0.2.0"\nentries:\n'
            "  - mention: savolitinib\n"
            "    enterprise_id: HMD:ENT:DC:savolitinib\n"
            "    aliases:\n      - savolitinib\n"
        ),
        encoding="utf-8",
    )
    props = tmp_path / "props.jsonl"
    save_proposals(
        props,
        [
            {
                "proposal_id": "HMDPROP:e2e",
                "mention": "e2e-slo-alias",
                "op": "create_synonym",
                "write_surface": "dictionary",
                "target_enterprise_id": "HMD:ENT:DC:savolitinib",
                "risk_tier": "L1",
                "status": "approved",
            }
        ],
    )
    dry = apply_approved(props, dry_run=True, dictionary_path=dict_path)
    assert dry.written
    assert called == []
    written = apply_approved(props, dry_run=False, dictionary_path=dict_path)
    assert any(w["action"] == "append_alias" for w in written.written)
    assert any(
        c.get("resolve_status") == "mapped" and c.get("mention") == "e2e-slo-alias" for c in called
    )


def test_export_matches_emits_mapped(tmp_path: Path, monkeypatch) -> None:
    import json

    from biomed_ontology.foundation.zingg_io import export_matches

    called: list[dict] = []
    monkeypatch.setattr(
        "biomed_ontology.lake.obs_events.emit_er_observation",
        lambda **kwargs: called.append(kwargs),
    )
    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        json.dumps(
            {
                "mention": "savolitinb",
                "enterprise_id": "HMD:ENT:DC:savolitinib",
                "score": 0.91,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "matches.jsonl"
    summary = export_matches(source=raw, out_path=out, min_score=0.8)
    assert summary["written"] >= 1
    assert any(
        c.get("resolve_status") == "mapped" and c.get("mention") == "savolitinb" for c in called
    )
