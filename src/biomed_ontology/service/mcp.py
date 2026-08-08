"""FastMCP：KB tools 与 Foundation ops 共用同一 MCP server。

KB 工具走 `dispatch`（契约 + 许可 + 观测包裹链）；
Foundation 工具直接调 FoundationApi（后端不可达时明确报错，无 YAML fallback）。
"""

from __future__ import annotations

from typing import Any

from biomed_ontology.foundation.api import SEMANTIC_OPS
from biomed_ontology.service.deps import get_state, parse_entitlements
from biomed_ontology.tools import TOOL_SPECS, dispatch

__all__ = ["create_mcp"]


def create_mcp(name: str = "hmd-semantic") -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(name)
    for spec in TOOL_SPECS:
        _register_kb(mcp, spec)
    for op in SEMANTIC_OPS:
        _register_foundation(mcp, op["name"], op["summary"])
    return mcp


def _register_kb(mcp: Any, spec: dict[str, Any]) -> None:
    tool_name = spec["name"]

    def tool(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        st = get_state()
        return dispatch(
            st.api,
            tool_name,
            arguments or {},
            entitlements=parse_entitlements(None, config=st.config),
        )

    tool.__name__ = tool_name
    tool.__doc__ = spec["summary"]
    mcp.tool(name=tool_name, description=spec["summary"])(tool)


def _register_foundation(mcp: Any, name: str, summary: str) -> None:
    """按 op 名注册；参数形状与 REST 对齐。"""

    if name == "resolve_entity":

        def resolve_entity(text: str, type_hint: str | None = None) -> dict[str, Any]:
            return _foundation().resolve_entity(text, type_hint=type_hint)

        resolve_entity.__doc__ = summary
        mcp.tool(name=name, description=summary)(resolve_entity)
        return

    if name in {
        "get_entity",
        "find_related_entities",
        "get_entity_evidence",
        "get_entity_assets",
        "get_entity_context",
    }:

        def entity_op(enterprise_id: str, _name: str = name) -> dict[str, Any]:
            return getattr(_foundation(), _name)(enterprise_id)

        entity_op.__name__ = name
        entity_op.__doc__ = summary
        mcp.tool(name=name, description=summary)(entity_op)
        return

    if name == "get_relationships":

        def get_relationships(enterprise_id: str, predicate: str | None = None) -> dict[str, Any]:
            return _foundation().get_relationships(enterprise_id, predicate=predicate)

        get_relationships.__doc__ = summary
        mcp.tool(name=name, description=summary)(get_relationships)
        return

    if name == "search_evidence":

        def search_evidence(
            query: str | None = None,
            entity_ids: list[str] | None = None,
            require_quote: bool = True,
        ) -> dict[str, Any]:
            return _foundation().search_evidence(
                query=query, entity_ids=entity_ids, require_quote=require_quote
            )

        search_evidence.__doc__ = summary
        mcp.tool(name=name, description=summary)(search_evidence)
        return

    if name == "search_assets":

        def search_assets(
            query: str | None = None, entity_ids: list[str] | None = None
        ) -> dict[str, Any]:
            return _foundation().search_assets(query=query, entity_ids=entity_ids)

        search_assets.__doc__ = summary
        mcp.tool(name=name, description=summary)(search_assets)
        return


def _foundation() -> Any:
    st = get_state()
    if st.foundation is None:
        raise RuntimeError("foundation not loaded")
    return st.foundation
