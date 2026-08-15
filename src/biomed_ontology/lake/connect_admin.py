"""Iceberg Kafka Connect 生命周期（pause/resume/status）。Connect 未起不阻断入仓。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from biomed_ontology.config import Settings, settings

__all__ = [
    "EXPECTED_ICEBERG_SINKS",
    "connectors_healthy",
    "list_status",
    "pause",
    "paused_iceberg_sinks",
    "resume",
]

_LOG = logging.getLogger(__name__)
EXPECTED_ICEBERG_SINKS = (
    "hmd-obs-tool-io",
    "hmd-obs-decision",
    "hmd-obs-span",
    "hmd-er-observations",
)
_CONNECT_TIMEOUT = 0.6
_UNREACHABLE_TTL_S = 30.0
_pause_depth = 0
_pause_lock = threading.Lock()
_unreach_until = 0.0


def _base(cfg: Settings | None = None) -> str:
    cfg = cfg or settings
    return (cfg.kafka_connect_url or "").rstrip("/")


def _mark_unreachable() -> None:
    global _unreach_until
    _unreach_until = time.monotonic() + _UNREACHABLE_TTL_S


def connect_reachable(cfg: Settings | None = None) -> bool:
    """Connect 不可达时短缓存，避免每篇文档写湖都空等。"""
    global _unreach_until
    if time.monotonic() < _unreach_until:
        return False
    url = _base(cfg)
    if not url:
        _mark_unreachable()
        return False
    try:
        with httpx.Client(timeout=_CONNECT_TIMEOUT) as client:
            r = client.get(f"{url}/connectors")
            r.raise_for_status()
        return True
    except Exception:
        _mark_unreachable()
        return False


def list_status(cfg: Settings | None = None) -> dict[str, Any]:
    url = _base(cfg)
    if not url:
        return {"_error": "HMD_KAFKA_CONNECT_URL empty"}
    try:
        with httpx.Client(timeout=_CONNECT_TIMEOUT) as client:
            r = client.get(f"{url}/connectors", params={"expand": "status"})
            r.raise_for_status()
            payload = r.json()
        return payload if isinstance(payload, dict) else {"_error": "unexpected status payload"}
    except Exception as exc:
        _mark_unreachable()
        return {"_error": f"{type(exc).__name__}: {exc}"}


def connectors_healthy(
    status: dict[str, Any],
    expected: tuple[str, ...] = EXPECTED_ICEBERG_SINKS,
) -> bool:
    if status.get("_error"):
        return False
    for name in expected:
        block = status.get(name)
        if not isinstance(block, dict):
            return False
        st = block.get("status") if isinstance(block.get("status"), dict) else block
        conn = st.get("connector") if isinstance(st.get("connector"), dict) else {}
        if str(conn.get("state") or "").upper() != "RUNNING":
            return False
        tasks = st.get("tasks") or []
        if not tasks:
            return False
        if any(str((t or {}).get("state") or "").upper() != "RUNNING" for t in tasks):
            return False
    return True


def _put_action(name: str, action: str, cfg: Settings | None = None) -> str:
    url = _base(cfg)
    if not url:
        return "skipped: no connect url"
    try:
        with httpx.Client(timeout=_CONNECT_TIMEOUT) as client:
            r = client.put(f"{url}/connectors/{name}/{action}")
            if r.status_code == 404:
                return "missing"
            r.raise_for_status()
        return action
    except Exception as exc:
        _LOG.warning("connect %s %s: %s", action, name, exc)
        _mark_unreachable()
        return f"error: {exc}"


def pause(
    names: tuple[str, ...] | list[str] | None = None,
    *,
    cfg: Settings | None = None,
) -> dict[str, str]:
    targets = tuple(names) if names else EXPECTED_ICEBERG_SINKS
    if not connect_reachable(cfg):
        return {name: "skipped: connect down" for name in targets}
    return {name: _put_action(name, "pause", cfg) for name in targets}


def resume(
    names: tuple[str, ...] | list[str] | None = None,
    *,
    cfg: Settings | None = None,
) -> dict[str, str]:
    targets = tuple(names) if names else EXPECTED_ICEBERG_SINKS
    if not connect_reachable(cfg):
        return {name: "skipped: connect down" for name in targets}
    return {name: _put_action(name, "resume", cfg) for name in targets}


@contextmanager
def paused_iceberg_sinks(cfg: Settings | None = None) -> Iterator[None]:
    """可重入：Connect 不可达只打 warning，不阻断写湖。"""
    global _pause_depth
    with _pause_lock:
        entering = _pause_depth == 0
        _pause_depth += 1
    if entering:
        pause(cfg=cfg)
    try:
        yield
    finally:
        with _pause_lock:
            _pause_depth -= 1
            leaving = _pause_depth == 0
        if leaving:
            resume(cfg=cfg)
