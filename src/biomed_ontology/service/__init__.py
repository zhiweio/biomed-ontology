"""服务层：CLI 之外的唯一 HTTP/MCP 入口，共用同一个 Semantic Access Layer。"""

from __future__ import annotations

from biomed_ontology.service.deps import (
    ServiceState,
    build_state,
    get_state,
    parse_entitlements,
    set_state,
)

__all__ = [
    "ServiceState",
    "build_state",
    "create_app",
    "create_mcp",
    "get_state",
    "parse_entitlements",
    "set_state",
]


def create_app(**kwargs):
    from biomed_ontology.service.app import create_app as _create

    return _create(**kwargs)


def create_mcp(*args, **kwargs):
    from biomed_ontology.service.mcp import create_mcp as _create

    return _create(*args, **kwargs)
