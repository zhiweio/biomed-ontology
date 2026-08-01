"""进程级单例。

KB 构建要读全部语料、建索引、跑 SHACL，每请求重建会让 P95 变成秒级。
但更要紧的是 `feedback_log` 与 `hub`：它们必须**跨请求共享**，
否则每个请求各写各的内存，本体演化信号一条也挖不出来 ——
而"形成 data loop"正是这套底座存在的理由。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from biomed_ontology.agentapi import AgentApi
from biomed_ontology.config import Settings, settings

__all__ = ["ServiceState", "build_state", "get_state", "parse_entitlements", "set_state"]

_state: ServiceState | None = None


@dataclass
class ServiceState:
    api: AgentApi
    kb: Any
    config: Settings

    @property
    def hub(self) -> Any:
        return getattr(self.kb, "hub", None)


def build_state(*, config: Settings | None = None) -> ServiceState:
    from biomed_ontology.pipeline import build_knowledge_base

    cfg = config or settings
    kb = build_knowledge_base()
    return ServiceState(api=AgentApi.from_kb(kb), kb=kb, config=cfg)


def set_state(state: ServiceState | None) -> None:
    global _state
    _state = state


def get_state() -> ServiceState:
    if _state is None:
        raise RuntimeError("服务尚未初始化：应在 lifespan 中调用 set_state")
    return _state


def parse_entitlements(header: str | None, *, config: Settings | None = None) -> frozenset[str]:
    """解析客户端自述的采购凭据。

    **默认不信任**。`X-HMD-Entitlements` 由调用方自己填，任何人都能写上
    `MOCK_LICENSED` 然后拿到受限内容（OWASP A01 越权访问）。
    生产环境必须由网关按已认证身份注入，并关闭这个开关。
    """
    cfg = config or settings
    if not cfg.trust_entitlement_header:
        return frozenset()
    return frozenset(e.strip() for e in (header or "").split(",") if e.strip())
