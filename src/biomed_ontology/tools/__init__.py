"""Ontology Semantic Layer access — MCP/REST tools for external agents (not an agent runtime)."""

from biomed_ontology.tools.api import TOOL_SPECS, Feedback, ToolApi, ToolError
from biomed_ontology.tools.dispatch import (
    dispatch,
    mcp_tool_descriptors,
    openapi_spec,
    write_contract_bundle,
)

__all__ = [
    "TOOL_SPECS",
    "Feedback",
    "ToolApi",
    "ToolError",
    "dispatch",
    "mcp_tool_descriptors",
    "openapi_spec",
    "write_contract_bundle",
]
