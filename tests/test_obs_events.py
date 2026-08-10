"""ObsEventProducer Jsonl fallback（无 Redpanda）。"""

from __future__ import annotations

import json
from pathlib import Path

from biomed_ontology.lake import obs_events


def test_emit_er_observation_wal_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)

    from biomed_ontology.config import Settings

    cfg = Settings(
        kafka_bootstrap_servers="",
        obs_events_enabled=True,
        obs_wal_dir=tmp_path,
    )
    obs_events.emit_er_observation(
        mention="savolitinb",
        source="runtime_resolve",
        resolve_status="unmapped",
        cfg=cfg,
    )
    wal = tmp_path / "hmd_er_observations.jsonl"
    assert wal.exists()
    row = json.loads(wal.read_text(encoding="utf-8").splitlines()[0])
    assert row["mention"] == "savolitinb"
    assert row["mention_key"] == "savolitinb"
    assert row["source"] == "runtime_resolve"
    assert row["observation_id"]


def test_emit_tool_io_extracts_unmapped_spans(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)

    from biomed_ontology.config import Settings

    cfg = Settings(
        kafka_bootstrap_servers="",
        obs_events_enabled=True,
        obs_wal_dir=tmp_path,
    )
    obs_events.emit_tool_io(
        {
            "trace_id": "t1",
            "tool_name": "normalize_entity",
            "ontology_release_id": "r1",
            "status": "OK",
            "latency_ms": 1.0,
            "input_json": "{}",
            "output_json": json.dumps({"unmapped_spans": ["zzz-unknown"]}),
            "contract_valid": True,
        },
        cfg=cfg,
    )
    tool_wal = tmp_path / "hmd_obs_tool_io.jsonl"
    er_wal = tmp_path / "hmd_er_observations.jsonl"
    assert tool_wal.exists()
    assert er_wal.exists()
    er = json.loads(er_wal.read_text(encoding="utf-8").splitlines()[0])
    assert er["mention"] == "zzz-unknown"
    assert er["source"] == "runtime_normalize"


def test_obs_events_disabled_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings

    cfg = Settings(obs_events_enabled=False, obs_wal_dir=tmp_path)
    obs_events.emit_er_observation(
        mention="nope",
        source="runtime_resolve",
        resolve_status="unmapped",
        cfg=cfg,
    )
    assert not list(tmp_path.glob("*.jsonl"))
