"""ObsEventProducer Jsonl fallback（无 Redpanda）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


class _FakeKafka:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes | None, bytes | None]] = []
        self.flush_remaining = 0
        self.fail_produce = False

    def produce(self, topic: str, value=None, key=None) -> None:
        if self.fail_produce:
            raise RuntimeError("produce boom")
        self.messages.append((topic, value, key))

    def poll(self, _timeout: float) -> int:
        return 0

    def flush(self, _timeout: float = 5.0) -> int:
        return self.flush_remaining


def test_replay_obs_wal_dry_run_counts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings

    cfg = Settings(kafka_bootstrap_servers="", obs_events_enabled=True, obs_wal_dir=tmp_path)
    (tmp_path / "hmd_er_observations.jsonl").write_text(
        '{"mention":"a","observation_id":"1"}\n{"mention":"b","observation_id":"2"}\n',
        encoding="utf-8",
    )
    result = obs_events.replay_obs_wal(cfg=cfg, dry_run=True)
    assert result["dry_run"] is True
    assert result["total_lines"] == 2
    assert result["lines"] == 2
    assert (tmp_path / "hmd_er_observations.jsonl").exists()


def test_replay_obs_wal_archives_on_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings

    cfg = Settings(
        kafka_bootstrap_servers="127.0.0.1:19092",
        obs_events_enabled=True,
        obs_wal_dir=tmp_path,
    )
    wal = tmp_path / "hmd_er_observations.jsonl"
    wal.write_text('{"mention":"x","observation_id":"1"}\n', encoding="utf-8")
    fake = _FakeKafka()

    def _init(self, cfg=None) -> None:
        self.cfg = cfg
        self._kafka = fake
        self._kafka_err = None

    monkeypatch.setattr(obs_events.ObsEventProducer, "__init__", _init)
    monkeypatch.setattr(obs_events, "probe_kafka", lambda cfg=None: None)
    result = obs_events.replay_obs_wal(cfg=cfg)
    assert result["produced_n"] == 1
    assert not wal.exists()
    replayed = list((tmp_path / "replayed").rglob("*.jsonl"))
    assert replayed
    assert "x" in replayed[0].read_text(encoding="utf-8")
    assert fake.messages[0][0] == "hmd.er.observations"


def test_replay_obs_wal_keeps_file_when_flush_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings

    cfg = Settings(
        kafka_bootstrap_servers="127.0.0.1:19092",
        obs_events_enabled=True,
        obs_wal_dir=tmp_path,
    )
    wal = tmp_path / "hmd_obs_tool_io.jsonl"
    wal.write_text('{"trace_id":"t1"}\n', encoding="utf-8")
    fake = _FakeKafka()
    fake.flush_remaining = 2

    def _init(self, cfg=None) -> None:
        self.cfg = cfg
        self._kafka = fake
        self._kafka_err = None

    monkeypatch.setattr(obs_events.ObsEventProducer, "__init__", _init)
    monkeypatch.setattr(obs_events, "probe_kafka", lambda cfg=None: None)
    with pytest.raises(RuntimeError, match="flush"):
        obs_events.replay_obs_wal(cfg=cfg)
    assert wal.exists()
    assert "t1" in wal.read_text(encoding="utf-8")


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
