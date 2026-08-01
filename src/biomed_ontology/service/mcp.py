"""FastMCP 服务：与 REST 共用 `dispatch`，因此两条链路不可能出现行为差异。

如果 MCP 侧另写一遍参数处理，许可过滤、trace 记录、信封字段就会各走各的，
而"MCP 上能拿到 REST 上拿不到的内容"是最难发现的越权形态。
"""

from __future__ import annotations

from typing import Any

from biomed_ontology.agentapi import TOOL_SPECS
from biomed_ontology.agentapi.serve import dispatch
from biomed_ontology.service.deps import get_state, parse_entitlements

__all__ = ["create_mcp"]


def create_mcp(name: str = "hmd-biomed-ontology") -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(name)
    for spec in TOOL_SPECS:
        _register(mcp, spec)
    return mcp


def _register(mcp: Any, spec: dict[str, Any]) -> None:
    tool_name = spec["name"]

    def tool(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        st = get_state()
        # MCP 无 HTTP 头，凭据只能来自服务端配置 —— 不接受客户端在参数里自述，
        # 那等于把许可边界交给调用方。
        return dispatch(
            st.api,
            tool_name,
            arguments or {},
            entitlements=parse_entitlements(None, config=st.config),
        )

    tool.__name__ = tool_name
    tool.__doc__ = spec["summary"]
    mcp.tool(name=tool_name, description=spec["summary"])(tool)
