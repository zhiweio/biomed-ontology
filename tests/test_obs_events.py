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


def test_emit_decisions_projects_why_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings

    cfg = Settings(kafka_bootstrap_servers="", obs_events_enabled=True, obs_wal_dir=tmp_path)
    candidates = [
        {"candidate_id": f"c{i}", "score": 0.1 * i, "channel": "bm25", "label": "x"}
        for i in range(12)
    ]
    obs_events.emit_decisions(
        [
            {
                "trace_id": "t-dec",
                "step_seq": 0,
                "stage": "LLM",
                "justification": "LLMDisambiguation",
                "chosen": "c0",
                "candidates": candidates,
                "state_before": {"text": "x" * 5000, "noise": "drop-me"},
                "state_after": {"ok": True},
            }
        ],
        cfg=cfg,
    )
    wal = tmp_path / "hmd_obs_decision.jsonl"
    row = json.loads(wal.read_text(encoding="utf-8").splitlines()[0])
    slim = json.loads(row["candidates_json"])
    assert row["candidates_n"] == 12
    assert len(slim) == 8
    assert slim[0]["id"] == "c11"
    assert "c0" in {c["id"] for c in slim}
    assert row["subject_text"] == "x" * 256
    assert row["state_before"] is None
    assert row["state_after"] is None
    fields = set((row["truncated_fields"] or "").split(","))
    assert {"subject", "candidates", "state_before", "state_after"} <= fields
    if row["state_before"] is not None:
        json.loads(row["state_before"])


def test_emit_spans_wal_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings
    from biomed_ontology.observability import Span

    cfg = Settings(kafka_bootstrap_servers="", obs_events_enabled=True, obs_wal_dir=tmp_path)
    obs_events.emit_spans(
        [
            Span(
                span_id="s1",
                trace_id="t-span",
                name="normalize",
                parent_id=None,
                start_ms=0.0,
                end_ms=12.0,
                status="OK",
            )
        ],
        cfg=cfg,
    )
    wal = tmp_path / "hmd_obs_span.jsonl"
    row = json.loads(wal.read_text(encoding="utf-8").splitlines()[0])
    assert row["span_id"] == "s1"
    assert row["name"] == "normalize"
    assert row["duration_ms"] == 12.0
    assert row.get("truncated_fields") in (None, "")


def test_emit_spans_keeps_allowlisted_attributes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_events, "_producer", None)
    from biomed_ontology.config import Settings
    from biomed_ontology.observability import Span

    cfg = Settings(kafka_bootstrap_servers="", obs_events_enabled=True, obs_wal_dir=tmp_path)
    obs_events.emit_spans(
        [
            Span(
                span_id="s2",
                trace_id="t-span",
                name="normalize.llm",
                start_ms=0.0,
                end_ms=3.0,
                attributes={
                    "ontology.release_id": "0.1.0",
                    "hmd.stage": "LLM",
                    "doc": "drop-me",
                    "error.message": "boom",
                },
            )
        ],
        cfg=cfg,
    )
    row = json.loads((tmp_path / "hmd_obs_span.jsonl").read_text(encoding="utf-8").splitlines()[0])
    attrs = json.loads(row["attributes_json"])
    assert attrs["ontology.release_id"] == "0.1.0"
    assert attrs["hmd.stage"] == "LLM"
    assert attrs["error.message"] == "boom"
    assert "doc" not in attrs
    assert "attributes" in (row["truncated_fields"] or "")


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
