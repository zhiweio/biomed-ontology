"""Runtime / ER 观测事件：Kafka-API produce → Redpanda（Iceberg Connect Sink 入湖）。

热路径禁止同步 PyIceberg append。broker 不可达时落 Jsonl fallback，不阻断请求。
"""

from __future__ import annotations

import atexit
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
    "count_wal_lines",
    "emit_decisions",
    "emit_er_observation",
    "emit_spans",
    "emit_tool_io",
    "flush_obs_events",
    "get_obs_producer",
    "list_wal_files",
    "observation_id_for",
    "probe_kafka",
    "replay_obs_wal",
    "topic_from_wal_filename",
    "wal_dir",
]

_CANDIDATE_MAX = 8
_STATE_MAX_BYTES = 2048
_STATE_KEYS = frozenset({"text", "concept_id", "query"})
_ATTR_EXACT = frozenset({"ontology.release_id", "error.message"})

_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_producer: ObsEventProducer | None = None
_atexit_registered = False


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
                        # 短 CLI 进程：尽快发出，避免 terminate 时仍在队列
                        "acks": "1",
                        "linger.ms": 5,
                        "batch.num.messages": 100,
                        "message.timeout.ms": 5000,
                        "socket.connection.setup.timeout.ms": 3000,
                    }
                )
            except Exception as exc:
                self._kafka_err = f"{type(exc).__name__}: {exc}"
                _LOG.warning("obs kafka producer unavailable: %s", self._kafka_err)

    def produce(self, topic: str, record: dict[str, Any]) -> None:
        if not self.cfg.obs_events_enabled:
            return
        payload = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")
        key = str(
            record.get("observation_id")
            or record.get("decision_id")
            or record.get("span_id")
            or record.get("trace_id")
            or ""
        ).encode("utf-8")
        if self._kafka is not None:
            try:
                self._kafka.produce(topic, value=payload, key=key or None)
                self._kafka.poll(0)
                return
            except Exception as exc:
                _LOG.warning("obs kafka produce failed topic=%s: %s", topic, exc)
        self._wal_append(topic, record)

    def produce_kafka_only(self, topic: str, record: dict[str, Any]) -> None:
        """回放路径：失败上抛，禁止再写 WAL。"""
        if self._kafka is None:
            raise RuntimeError(self._kafka_err or "obs kafka producer unavailable")
        payload = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")
        key = str(
            record.get("observation_id")
            or record.get("decision_id")
            or record.get("span_id")
            or record.get("trace_id")
            or ""
        ).encode("utf-8")
        self._kafka.produce(topic, value=payload, key=key or None)
        self._kafka.poll(0)

    def flush(self, timeout: float = 5.0) -> None:
        if self._kafka is not None:
            with contextlib.suppress(Exception):
                remaining = self._kafka.flush(timeout)
                if remaining:
                    _LOG.warning("obs kafka flush: %s message(s) still in queue", remaining)

    def flush_strict(self, timeout: float = 10.0) -> None:
        if self._kafka is None:
            raise RuntimeError(self._kafka_err or "obs kafka producer unavailable")
        remaining = int(self._kafka.flush(timeout) or 0)
        if remaining:
            raise RuntimeError(f"obs kafka flush: {remaining} message(s) still in queue")

    def _wal_append(self, topic: str, record: dict[str, Any]) -> None:
        path = wal_dir(self.cfg) / f"{topic.replace('.', '_')}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def get_obs_producer(cfg: Settings | None = None) -> ObsEventProducer:
    global _producer, _atexit_registered
    if _producer is None:
        with _lock:
            if _producer is None:
                _producer = ObsEventProducer(cfg)
                if not _atexit_registered:
                    atexit.register(flush_obs_events)
                    _atexit_registered = True
    return _producer


def flush_obs_events(timeout: float = 5.0) -> None:
    """进程退出 / CLI 结束前刷出 Kafka 队列（避免 Producer terminating 丢消息）。"""
    prod = _producer
    if prod is not None:
        prod.flush(timeout)


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
    producer = get_obs_producer(cfg)
    producer.produce(cfg.kafka_obs_tool_io_topic, row)

    # 从 normalize 等输出抽 unmapped_spans → er_observations
    try:
        out = json.loads(str(record.get("output_json") or "{}"))
    except json.JSONDecodeError:
        producer.flush()
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
            flush=False,
        )
    producer.flush()


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
    flush: bool = True,
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
    producer = get_obs_producer(cfg)
    producer.produce(cfg.kafka_er_observations_topic, row)
    if flush:
        producer.flush()


def _clip_chars(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _project_state(value: Any) -> tuple[str | None, bool]:
    """结构化 state 只留白名单 key；超预算整段丢掉，禁止半截 JSON。"""
    if value is None or value == "":
        return None, False
    if isinstance(value, str):
        clipped, trunc = _clip_chars(value, 256)
        return clipped, trunc
    if isinstance(value, dict):
        dropped = any(key not in _STATE_KEYS for key in value)
        slim = {key: value[key] for key in _STATE_KEYS if key in value}
        if not slim:
            return None, True
        raw = json.dumps(slim, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > _STATE_MAX_BYTES:
            return None, True
        return raw, dropped
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > _STATE_MAX_BYTES:
        return None, True
    return raw, False


def _norm_candidate(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        cid = item.get("id") or item.get("candidate_id")
        score = item.get("score")
        channel = item.get("channel")
        label = item.get("label")
    else:
        cid = getattr(item, "candidate_id", None)
        score = getattr(item, "score", None)
        channel = getattr(item, "channel", None)
        label = getattr(item, "label", None)
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    return {
        "id": str(cid) if cid else None,
        "score": score_f,
        "channel": channel,
        "label": label,
    }


def _project_candidates(candidates: Any, chosen: Any) -> tuple[str, int, bool]:
    """按 score 降序取 K 条，必留 chosen；``candidates_n`` 是原始条数。"""
    items = [_norm_candidate(item) for item in list(candidates or [])]
    total = len(items)
    items.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0.0)))
    top = items[:_CANDIDATE_MAX]
    chosen_s = str(chosen) if chosen else ""
    ids = {row["id"] for row in top if row["id"]}
    truncated = total > _CANDIDATE_MAX
    if chosen_s and chosen_s not in ids:
        chosen_row = next(
            (row for row in items if row["id"] == chosen_s),
            {"id": chosen_s, "score": None, "channel": None, "label": None},
        )
        if len(top) < _CANDIDATE_MAX:
            top.append(chosen_row)
        else:
            top[-1] = chosen_row
        truncated = True
    return json.dumps(top, ensure_ascii=False, default=str), total, truncated


def _project_attributes(attrs: Any) -> tuple[str | None, bool]:
    if not attrs:
        return None, False
    if not isinstance(attrs, dict):
        return None, True
    kept: dict[str, Any] = {}
    dropped = False
    for key, value in attrs.items():
        name = str(key)
        if name in _ATTR_EXACT or name.startswith("hmd."):
            kept[name] = value
        else:
            dropped = True
    if not kept:
        return None, dropped
    raw = json.dumps(kept, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > _STATE_MAX_BYTES:
        return None, True
    return raw, dropped


def _truncated_fields(*pairs: tuple[str, bool]) -> str | None:
    names = [name for name, flag in pairs if flag]
    return ",".join(names) if names else None


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    to_json = getattr(record, "to_json", None)
    if callable(to_json):
        payload = to_json()
        if isinstance(payload, dict):
            return payload
    return {
        "span_id": getattr(record, "span_id", None),
        "trace_id": getattr(record, "trace_id", None),
        "name": getattr(record, "name", None),
        "parent_id": getattr(record, "parent_id", None),
        "duration_ms": getattr(record, "duration_ms", None),
        "status": getattr(record, "status", None),
        "attributes": getattr(record, "attributes", None),
    }


def emit_decisions(
    records: list[Any] | None,
    *,
    cfg: Settings | None = None,
    flush: bool = True,
) -> None:
    """Hub DecisionRecord → topic hmd.obs.decision。WHY 投影，不按字节切 JSON。"""
    from biomed_ontology.observability import subject_from_state

    cfg = cfg or settings
    if not cfg.obs_events_enabled or not records:
        return
    event_ts, event_date = _now_parts()
    producer = get_obs_producer(cfg)
    for record in records:
        payload = _record_dict(record)
        trace_id = str(payload.get("trace_id") or "")
        step_seq = int(payload.get("step_seq") or 0)
        stage = str(payload.get("stage") or "")
        justification = payload.get("justification")
        if hasattr(justification, "value"):
            justification = justification.value
        subject, subject_trunc = subject_from_state(payload.get("state_before"))
        if not subject and payload.get("subject_text"):
            subject, subject_trunc = subject_from_state(
                None, subject_text=str(payload.get("subject_text"))
            )
        candidates_json, candidates_n, cands_trunc = _project_candidates(
            payload.get("candidates"), payload.get("chosen")
        )
        state_before, before_trunc = _project_state(payload.get("state_before"))
        state_after, after_trunc = _project_state(payload.get("state_after"))
        row = {
            "decision_id": hashlib.sha1(f"{trace_id}|{step_seq}|{stage}".encode()).hexdigest()[:24],
            "trace_id": trace_id,
            "step_seq": step_seq,
            "stage": stage,
            "justification": str(justification or ""),
            "chosen": payload.get("chosen"),
            "span_id": payload.get("span_id"),
            "subject_text": subject or None,
            "candidates_json": candidates_json,
            "candidates_n": candidates_n,
            "state_before": state_before,
            "state_after": state_after,
            "confidence": payload.get("confidence"),
            "rule_id": payload.get("rule_id"),
            "model_id": payload.get("model_id"),
            "elapsed_ms": float(payload.get("elapsed_ms") or 0.0),
            "truncated_fields": _truncated_fields(
                ("subject", subject_trunc),
                ("candidates", cands_trunc),
                ("state_before", before_trunc),
                ("state_after", after_trunc),
            ),
            "event_ts": event_ts,
            "ingested_at": event_ts,
            "event_date": event_date,
        }
        producer.produce(cfg.kafka_obs_decision_topic, row)
    if flush:
        producer.flush()


def emit_spans(
    records: list[Any] | None,
    *,
    cfg: Settings | None = None,
    flush: bool = True,
) -> None:
    """Hub Span → topic hmd.obs.span。"""
    cfg = cfg or settings
    if not cfg.obs_events_enabled or not records:
        return
    event_ts, event_date = _now_parts()
    producer = get_obs_producer(cfg)
    for record in records:
        payload = _record_dict(record)
        attrs_json, attrs_trunc = _project_attributes(payload.get("attributes"))
        row = {
            "span_id": str(payload.get("span_id") or ""),
            "trace_id": str(payload.get("trace_id") or ""),
            "name": str(payload.get("name") or ""),
            "parent_id": payload.get("parent_id"),
            "duration_ms": float(payload.get("duration_ms") or 0.0),
            "status": str(payload.get("status") or "OK"),
            "attributes_json": attrs_json,
            "truncated_fields": _truncated_fields(("attributes", attrs_trunc)),
            "event_ts": event_ts,
            "ingested_at": event_ts,
            "event_date": event_date,
        }
        if not row["span_id"]:
            continue
        producer.produce(cfg.kafka_obs_span_topic, row)
    if flush:
        producer.flush()


def topic_from_wal_filename(name: str) -> str:
    """``hmd_obs_tool_io.jsonl`` → ``hmd.obs.tool_io``。"""
    return Path(name).stem.replace("_", ".")


def list_wal_files(cfg: Settings | None = None) -> list[Path]:
    root = wal_dir(cfg)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.jsonl") if p.is_file() and "replayed" not in p.parts)


def count_wal_lines(cfg: Settings | None = None) -> int:
    total = 0
    for path in list_wal_files(cfg):
        total += sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    return total


def probe_kafka(cfg: Settings | None = None) -> None:
    """broker 不可达则抛错，不删 WAL。"""
    cfg = cfg or settings
    servers = (cfg.kafka_bootstrap_servers or "").strip()
    if not servers:
        raise RuntimeError("obs wal replay: HMD_KAFKA_BOOTSTRAP_SERVERS empty")
    from confluent_kafka.admin import AdminClient

    admin = AdminClient(
        {
            "bootstrap.servers": servers,
            "socket.connection.setup.timeout.ms": 3000,
        }
    )
    admin.list_topics(timeout=5)


def _archive_wal_lines(dest_dir: Path, filename: str, lines: list[str]) -> None:
    if not lines:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    with dest.open("a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def _rewrite_wal(path: Path, lines: list[str]) -> None:
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def replay_obs_wal(
    *,
    max_lines: int | None = None,
    dry_run: bool = False,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    """把 Jsonl WAL produce 回原 topic；成功归档，不直写 Iceberg。"""
    cfg = cfg or settings
    limit = int(max_lines if max_lines is not None else cfg.obs_wal_replay_max_lines)
    files = list_wal_files(cfg)
    planned: list[tuple[Path, list[str]]] = []
    for path in files:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            planned.append((path, lines))
    total_lines = sum(len(lines) for _, lines in planned)
    if dry_run:
        return {
            "dry_run": True,
            "files": len(planned),
            "lines": min(total_lines, limit),
            "total_lines": total_lines,
            "produced_n": 0,
            "archived_n": 0,
        }

    probe_kafka(cfg)
    producer = ObsEventProducer(cfg)
    if producer._kafka is None:
        raise RuntimeError(producer._kafka_err or "obs wal replay: producer unavailable")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archived_dir = wal_dir(cfg) / "replayed" / stamp
    budget = limit
    produced_n = 0
    archived_n = 0
    files_done: list[str] = []

    for path, lines in planned:
        if budget <= 0:
            break
        take = lines[:budget]
        rest = lines[budget:]
        topic = topic_from_wal_filename(path.name)
        done: list[str] = []
        try:
            for ln in take:
                record = json.loads(ln)
                if not isinstance(record, dict):
                    raise RuntimeError(f"wal line is not an object: {path.name}")
                producer.produce_kafka_only(topic, record)
                done.append(ln)
        except Exception as exc:
            flushed = False
            with contextlib.suppress(Exception):
                producer.flush_strict()
                flushed = True
            if flushed:
                _archive_wal_lines(archived_dir, path.name, done)
                archived_n += len(done)
                produced_n += len(done)
                _rewrite_wal(path, take[len(done) :] + rest)
            else:
                _rewrite_wal(path, take + rest)
            raise RuntimeError(f"obs wal replay failed file={path.name}: {exc}") from exc
        try:
            producer.flush_strict()
        except Exception as exc:
            _rewrite_wal(path, take + rest)
            raise RuntimeError(f"obs wal replay failed file={path.name}: {exc}") from exc
        _archive_wal_lines(archived_dir, path.name, done)
        _rewrite_wal(path, rest)
        produced_n += len(done)
        archived_n += len(done)
        budget -= len(done)
        files_done.append(path.name)

    return {
        "dry_run": False,
        "files": len(files_done),
        "lines": produced_n,
        "total_lines": total_lines,
        "produced_n": produced_n,
        "archived_n": archived_n,
        "archived_dir": str(archived_dir) if archived_n else None,
        "files_done": files_done,
    }
