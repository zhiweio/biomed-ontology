"""Foundation Semantic API — FastAPI + 可选 MCP 挂载。

与现有 agentapi 并行：本服务只提供 World Model 语义操作，不做 Agent 编排。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from biomed_ontology.foundation.api import SEMANTIC_OPS, FoundationApi
from biomed_ontology.foundation.world import load_world_model

__all__ = ["create_foundation_app"]


class ResolveBody(BaseModel):
    text: str
    type_hint: str | None = None


class EntityBody(BaseModel):
    enterprise_id: str


class RelationshipsBody(BaseModel):
    enterprise_id: str
    predicate: str | None = None


class EvidenceSearchBody(BaseModel):
    query: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    require_quote: bool = True


class AssetSearchBody(BaseModel):
    query: str | None = None
    entity_ids: list[str] = Field(default_factory=list)


def create_foundation_app(
    *,
    bern2_url: str | None = None,
    mcp_app: Any = None,
) -> FastAPI:
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        world = load_world_model(bern2_url=bern2_url)
        state["api"] = FoundationApi(world)
        if mcp_app is None:
            yield
        else:
            # mount() 不会跑子应用 lifespan；与 PoC service/app.py 同样串起来。
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        state.clear()

    app = FastAPI(
        title="Asliva Enterprise Biomedical World Model",
        version="0.3.0",
        description="Semantic Access Layer for AI Agents（非 Agent Runtime）",
        lifespan=lifespan,
    )

    def api() -> FoundationApi:
        obj = state.get("api")
        if obj is None:
            raise HTTPException(503, "foundation not ready")
        return obj

    @app.get("/health")
    def health() -> dict[str, Any]:
        a = api()
        return {
            "status": "ok",
            "ontology_release_id": a.world.release_id,
            "ops": [o["name"] for o in SEMANTIC_OPS],
            "entities": len(a.world.entities),
            "mcp": mcp_app is not None,
        }

    @app.get("/v1/ops")
    def list_ops() -> dict[str, Any]:
        return {"ops": SEMANTIC_OPS}

    @app.post("/v1/resolve_entity")
    def resolve_entity(body: ResolveBody) -> dict[str, Any]:
        return api().resolve_entity(body.text, type_hint=body.type_hint)

    @app.post("/v1/get_entity")
    def get_entity(body: EntityBody) -> dict[str, Any]:
        return api().get_entity(body.enterprise_id)

    @app.post("/v1/get_relationships")
    def get_relationships(body: RelationshipsBody) -> dict[str, Any]:
        return api().get_relationships(body.enterprise_id, predicate=body.predicate)

    @app.post("/v1/find_related_entities")
    def find_related(body: EntityBody) -> dict[str, Any]:
        return api().find_related_entities(body.enterprise_id)

    @app.post("/v1/search_evidence")
    def search_evidence(body: EvidenceSearchBody) -> dict[str, Any]:
        return api().search_evidence(
            query=body.query,
            entity_ids=body.entity_ids or None,
            require_quote=body.require_quote,
        )

    @app.post("/v1/search_assets")
    def search_assets(body: AssetSearchBody) -> dict[str, Any]:
        return api().search_assets(query=body.query, entity_ids=body.entity_ids or None)

    @app.post("/v1/get_entity_evidence")
    def get_entity_evidence(body: EntityBody) -> dict[str, Any]:
        return api().get_entity_evidence(body.enterprise_id)

    @app.post("/v1/get_entity_assets")
    def get_entity_assets(body: EntityBody) -> dict[str, Any]:
        return api().get_entity_assets(body.enterprise_id)

    @app.post("/v1/get_entity_context")
    def get_entity_context(body: EntityBody) -> dict[str, Any]:
        return api().get_entity_context(body.enterprise_id)

    @app.get("/v1/golden_path")
    def golden_path(candidate: str = "HMPL-504") -> dict[str, Any]:
        return api().golden_path(candidate)

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    return app
