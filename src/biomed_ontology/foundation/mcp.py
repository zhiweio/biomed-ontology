"""Foundation MCP — 薄适配层，只暴露 Semantic Ops。"""

from __future__ import annotations

from typing import Any

from biomed_ontology.foundation.api import FoundationApi
from biomed_ontology.foundation.world import load_world_model

__all__ = ["create_foundation_mcp"]


def create_foundation_mcp(*, bern2_url: str | None = None) -> Any:
    from fastmcp import FastMCP

    api = FoundationApi(load_world_model(bern2_url=bern2_url))
    mcp = FastMCP("hmd-foundation")

    @mcp.tool()
    def resolve_entity(text: str, type_hint: str | None = None) -> dict[str, Any]:
        """文本 → Enterprise Entity ID。"""
        return api.resolve_entity(text, type_hint=type_hint)

    @mcp.tool()
    def get_entity(enterprise_id: str) -> dict[str, Any]:
        return api.get_entity(enterprise_id)

    @mcp.tool()
    def get_relationships(enterprise_id: str, predicate: str | None = None) -> dict[str, Any]:
        return api.get_relationships(enterprise_id, predicate=predicate)

    @mcp.tool()
    def find_related_entities(enterprise_id: str) -> dict[str, Any]:
        return api.find_related_entities(enterprise_id)

    @mcp.tool()
    def search_evidence(
        query: str | None = None,
        entity_ids: list[str] | None = None,
        require_quote: bool = True,
    ) -> dict[str, Any]:
        """Evidence Index 检索；默认要求可引用 quote。"""
        return api.search_evidence(query=query, entity_ids=entity_ids, require_quote=require_quote)

    @mcp.tool()
    def search_assets(
        query: str | None = None, entity_ids: list[str] | None = None
    ) -> dict[str, Any]:
        return api.search_assets(query=query, entity_ids=entity_ids)

    @mcp.tool()
    def get_entity_context(enterprise_id: str) -> dict[str, Any]:
        """聚合 entity + relationships + evidence + assets。"""
        return api.get_entity_context(enterprise_id)

    return mcp
