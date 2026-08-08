"""Foundation 检索可观测：structlog 四支柱。

- Trace(WHERE)  调用经过哪些阶段 / 后端
- IO(WHAT)      进出摘要（query、entity_ids、命中数）
- State(WHY)    为何走该后端、落选/失败原因、决策
- Metrics(WHEN) 延迟与计数

业务检索路径必须经 ``observe_retrieval`` 打点，禁止静默成功。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

__all__ = ["configure_foundation_logging", "get_logger", "observe_retrieval"]

_CONFIGURED = False


def configure_foundation_logging(*, json_logs: bool = True) -> None:
    """幂等配置 structlog（JSON 一行一事；写 stderr，避免污染 --json stdout）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str = "hmd.foundation") -> Any:
    configure_foundation_logging()
    return structlog.get_logger(name)


@contextmanager
def observe_retrieval(
    where: str,
    *,
    op: str,
    input_summary: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """检索操作上下文：进入时记 Trace，退出时记 IO / State / Metrics。

    调用方往 ``state`` 写入：
    - ``backend`` / ``backends``：实际后端（禁止 yaml）
    - ``why``：决策说明
    - ``output``：输出摘要（counts 等）
    - ``error``：失败信息（可选）
    """
    log = get_logger()
    t0 = time.perf_counter()
    state: dict[str, Any] = {"why": {}, "output": {}, "backend": None, "backends": None}
    log.info(
        "trace",
        pillar="trace",
        where=where,
        op=op,
        input=input_summary or {},
    )
    status = "ok"
    try:
        yield state
    except Exception as exc:
        status = "error"
        state["error"] = str(exc)
        state.setdefault("why", {})["error"] = str(exc)
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        backends = state.get("backends") or (
            {"primary": state["backend"]} if state.get("backend") else {}
        )
        why = dict(state.get("why") or {})
        if any(v == "yaml" for v in backends.values() if isinstance(v, str)):
            why["policy_violation"] = "yaml_fallback_forbidden"
        log.info(
            "io",
            pillar="io",
            where=where,
            op=op,
            what={
                "input": input_summary or {},
                "output": state.get("output") or {},
            },
        )
        log.info(
            "state",
            pillar="state",
            where=where,
            op=op,
            why=why,
            backends=backends,
            status=status,
        )
        log.info(
            "metrics",
            pillar="metrics",
            where=where,
            op=op,
            when={
                "elapsed_ms": elapsed_ms,
                "status": status,
                **{
                    k: v
                    for k, v in (state.get("output") or {}).items()
                    if isinstance(v, (int, float, bool))
                },
            },
        )
