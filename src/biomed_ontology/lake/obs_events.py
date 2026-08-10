"""Runtime / ER 观测事件：Kafka-API produce → Redpanda（Iceberg Connect Sink 入湖）。

热路径禁止同步 PyIceberg append。broker 不可达时落 Jsonl fallback，不阻断请求。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biomed_ontology.config import Settings, settings
from biomed_ontology.foundation.ids import normalize_alias_key

__all__ = [
    "ObsEventProducer",
    "emit_er_observation",
    "emit_tool_io",
    "get_obs_producer",
    "observation_id_for",
    "wal_dir",
]

_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_producer: ObsEventProducer | None = None


def wal_dir(cfg: Settings | None = None) -> Path:
    """本地 Jsonl WAL 目录（``HMD_OBS_WAL_DIR``）。"""
    cfg = cfg or settings
    return Path(cfg.obs_wal_dir)


def observation_id_for(
    *,
    source: str,
    mention_key: str,
    trace_id: str = "",
    event_ts: str = "",
) -> str:
    raw = f"{source}|{mention_key}|{trace_id}|{event_ts}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def _now_parts() -> tuple[str, str]:
    now = datetime.now(UTC)
    event_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return event_ts, now.strftime("%Y-%m-%d")


class ObsEventProducer:
    """Kafka-API producer（Redpanda）+ Jsonl WAL fallback。"""

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self._kafka = None
        self._kafka_err: str | None = None
        servers = (self.cfg.kafka_bootstrap_servers or "").strip()
        if servers:
            try:
                from confluent_kafka import Producer

                self._kafka = Producer(
                    {
                        "bootstrap.servers": servers,
                        "acks": "1",
                        "linger.ms": 50,
                        "batch.num.messages": 1000,
                        "message.timeout.ms": 5000,
                    }
                )
            except Exception as exc:
                self._kafka_err = f"{type(exc).__name__}: {exc}"
                _LOG.warning("obs kafka producer unavailable: %s", self._kafka_err)

    def produce(self, topic: str, record: dict[str, Any]) -> None:
        if not self.cfg.obs_events_enabled:
            return
        payload = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")
        key = str(record.get("observation_id") or record.get("trace_id") or "").encode("utf-8")
        if self._kafka is not None:
            try:
                self._kafka.produce(topic, value=payload, key=key or None)
                self._kafka.poll(0)
                return
            except Exception as exc:
                _LOG.warning("obs kafka produce failed topic=%s: %s", topic, exc)
        self._wal_append(topic, record)

    def flush(self, timeout: float = 2.0) -> None:
        if self._kafka is not None:
            with contextlib.suppress(Exception):
                self._kafka.flush(timeout)

    def _wal_append(self, topic: str, record: dict[str, Any]) -> None:
        path = wal_dir(self.cfg) / f"{topic.replace('.', '_')}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def get_obs_producer(cfg: Settings | None = None) -> ObsEventProducer:
    global _producer
    if _producer is None:
        with _lock:
            if _producer is None:
                _producer = ObsEventProducer(cfg)
    return _producer


def emit_tool_io(record: dict[str, Any], *, cfg: Settings | None = None) -> None:
    """Hub ToolIoRecord → topic hmd.obs.tool_io。"""
    cfg = cfg or settings
    if not cfg.obs_events_enabled:
        return
    event_ts, event_date = _now_parts()
    row = {
        "trace_id": str(record.get("trace_id") or ""),
        "tool_name": str(record.get("tool_name") or ""),
        "ontology_release_id": str(record.get("ontology_release_id") or ""),
        "status": str(record.get("status") or ""),
        "latency_ms": float(record.get("latency_ms") or 0.0),
        "agent_id": record.get("agent_id"),
        "session_id": record.get("session_id"),
        "input_json": record.get("input_json"),
        "output_json": record.get("output_json"),
        "error_message": record.get("error_message"),
        "contract_valid": (
            "true"
            if record.get("contract_valid") is True
            else "false"
            if record.get("contract_valid") is False
            else None
        ),
        "event_ts": event_ts,
        "ingested_at": event_ts,
        "event_date": event_date,
    }
    get_obs_producer(cfg).produce(cfg.kafka_obs_tool_io_topic, row)

    # 从 normalize 等输出抽 unmapped_spans → er_observations
    try:
        out = json.loads(str(record.get("output_json") or "{}"))
    except json.JSONDecodeError:
        return
    for span in out.get("unmapped_spans") or []:
        text = str(span).strip()
        if not text:
            continue
        emit_er_observation(
            mention=text,
            source="runtime_normalize",
            resolve_status="unmapped",
            tool_name=str(record.get("tool_name") or ""),
            trace_id=str(record.get("trace_id") or ""),
            ontology_release_id=str(record.get("ontology_release_id") or ""),
            cfg=cfg,
        )


def emit_er_observation(
    *,
    mention: str,
    source: str,
    resolve_status: str = "unmapped",
    kind_hint: str | None = None,
    confidence: float | None = None,
    tool_name: str | None = None,
    trace_id: str | None = None,
    document_id: str | None = None,
    chunk_id: str | None = None,
    bern2_ids: list[str] | None = None,
    ontology_release_id: str | None = None,
    cfg: Settings | None = None,
) -> None:
    cfg = cfg or settings
    if not cfg.obs_events_enabled:
        return
    mention = (mention or "").strip()
    if not mention:
        return
    event_ts, event_date = _now_parts()
    key = normalize_alias_key(mention)
    row = {
        "observation_id": observation_id_for(
            source=source, mention_key=key, trace_id=trace_id or "", event_ts=event_ts
        ),
        "mention": mention,
        "mention_key": key,
        "source": source,
        "resolve_status": resolve_status,
        "kind_hint": kind_hint,
        "confidence": confidence,
        "tool_name": tool_name,
        "trace_id": trace_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "bern2_ids": list(bern2_ids or []),
        "ontology_release_id": ontology_release_id,
        "event_ts": event_ts,
        "ingested_at": event_ts,
        "event_date": event_date,
    }
    get_obs_producer(cfg).produce(cfg.kafka_er_observations_topic, row)
