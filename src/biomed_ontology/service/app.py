"""FastAPI 应用：单一 Semantic Access Layer（KB tools + Foundation ops）。

KB 工具路由由 TOOL_SPECS 生成；Foundation Semantic Ops 并列挂载。
OpenAPI 以 LinkML 契约为主，并合并 Foundation ops 路径说明。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from biomed_ontology.config import Settings, settings
from biomed_ontology.foundation.api import SEMANTIC_OPS
from biomed_ontology.service.deps import (
    ServiceState,
    build_state,
    get_state,
    parse_entitlements,
    set_state,
)
from biomed_ontology.tools import TOOL_SPECS, dispatch, openapi_spec

__all__ = ["create_app"]


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


class LookupBiosBody(BaseModel):
    query: str | None = None
    external_id: str | None = None
    bios_curie: str | None = None
    max_surfaces: int = 8
    max_neighbors: int = 10
    include_enterprise_bridges: bool = True


def create_app(
    *,
    config: Settings | None = None,
    state: ServiceState | None = None,
    mcp_app: Any = None,
    bern2_url: str | None = None,
) -> FastAPI:
    cfg = config or settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        set_state(state or build_state(config=cfg, bern2_url=bern2_url))
        for warning in cfg.warnings():
            print(f"[WARN] {warning}")
        if mcp_app is None:
            yield
        else:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        set_state(None)

    app = FastAPI(
        title="Asliva Semantic Access Layer",
        version="0.3.0",
        description=(
            "Enterprise Biomedical World Model — Ontology Semantic Layer + Foundation ops "
            "for external agents（非 Agent Runtime）"
        ),
        lifespan=lifespan,
    )

    for spec in TOOL_SPECS:
        _register_kb_tool(app, spec, cfg)

    _register_foundation_routes(app)

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    @app.get("/health")
    def health() -> dict[str, Any]:
        st = get_state()
        ops = [o["name"] for o in SEMANTIC_OPS]
        return {
            "status": "ok",
            "ontology_release_id": st.kb.release_id,
            "tools": len(TOOL_SPECS),
            "foundation_ops": ops,
            "entities": len(st.world.entities) if st.world is not None else 0,
            "mcp": mcp_app is not None,
            "warnings": st.config.warnings(),
        }

    @app.get("/v1/ops")
    def list_ops() -> dict[str, Any]:
        return {
            "kb_tools": TOOL_SPECS,
            "foundation_ops": SEMANTIC_OPS,
        }

    app.openapi = lambda: _merged_openapi()  # ty: ignore[invalid-assignment]
    return app


def _register_kb_tool(app: FastAPI, spec: dict[str, Any], cfg: Settings) -> None:
    tool_name = spec["name"]

    async def endpoint(
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
        x_hmd_entitlements: str | None = Header(default=None),
        x_hmd_trace_id: str | None = Header(default=None),
        x_hmd_client_id: str | None = Header(default=None),
        x_hmd_agent_id: str | None = Header(default=None),  # X-HMD-Client-Id 别名
    ) -> JSONResponse:
        st = get_state()
        client_id = x_hmd_client_id or x_hmd_agent_id
        try:
            result = dispatch(
                st.api,
                tool_name,
                payload,
                entitlements=parse_entitlements(x_hmd_entitlements, config=st.config),
                client_id=client_id,
                trace_id=x_hmd_trace_id,
            )
        except TypeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(content=_jsonable(result))

    endpoint.__name__ = tool_name
    app.post(f"/v1/{tool_name}", name=tool_name, summary=spec["summary"])(endpoint)


def _foundation():
    st = get_state()
    if st.foundation is None:
        raise HTTPException(503, "foundation not loaded")
    return st.foundation


def _register_foundation_routes(app: FastAPI) -> None:
    @app.post("/v1/resolve_entity")
    def resolve_entity(body: ResolveBody) -> dict[str, Any]:
        return _foundation().resolve_entity(body.text, type_hint=body.type_hint)

    @app.post("/v1/lookup_bios_concept")
    def lookup_bios_concept(body: LookupBiosBody) -> dict[str, Any]:
        return _foundation().lookup_bios_concept(
            query=body.query,
            external_id=body.external_id,
            bios_curie=body.bios_curie,
            max_surfaces=body.max_surfaces,
            max_neighbors=body.max_neighbors,
            include_enterprise_bridges=body.include_enterprise_bridges,
        )

    @app.post("/v1/get_entity")
    def get_entity(body: EntityBody) -> dict[str, Any]:
        return _foundation().get_entity(body.enterprise_id)

    @app.post("/v1/get_relationships")
    def get_relationships(body: RelationshipsBody) -> dict[str, Any]:
        return _foundation().get_relationships(body.enterprise_id, predicate=body.predicate)

    @app.post("/v1/find_related_entities")
    def find_related(body: EntityBody) -> dict[str, Any]:
        return _foundation().find_related_entities(body.enterprise_id)

    @app.post("/v1/search_evidence")
    def search_evidence(body: EvidenceSearchBody) -> dict[str, Any]:
        return _foundation().search_evidence(
            query=body.query,
            entity_ids=body.entity_ids or None,
            require_quote=body.require_quote,
        )

    @app.post("/v1/search_assets")
    def search_assets(body: AssetSearchBody) -> dict[str, Any]:
        return _foundation().search_assets(query=body.query, entity_ids=body.entity_ids or None)

    @app.post("/v1/get_entity_evidence")
    def get_entity_evidence(body: EntityBody) -> dict[str, Any]:
        return _foundation().get_entity_evidence(body.enterprise_id)

    @app.post("/v1/get_entity_assets")
    def get_entity_assets(body: EntityBody) -> dict[str, Any]:
        return _foundation().get_entity_assets(body.enterprise_id)

    @app.post("/v1/get_entity_context")
    def get_entity_context(body: EntityBody) -> dict[str, Any]:
        return _foundation().get_entity_context(body.enterprise_id)

    @app.get("/v1/golden_path")
    def golden_path(candidate: str = "HMPL-504") -> dict[str, Any]:
        st = get_state()
        return _foundation().golden_path(candidate, tools=st.api)


def _merged_openapi() -> dict[str, Any]:
    """KB 工具走 LinkML 契约；Foundation ops 补路径，避免文档与路由漂移。"""
    spec = openapi_spec()
    for op in SEMANTIC_OPS:
        path = f"/v1/{op['name']}"
        if path in spec["paths"]:
            continue
        spec["paths"][path] = {
            "post": {
                "operationId": op["name"],
                "summary": op["summary"],
                "tags": ["foundation"],
                "responses": {"200": {"description": "OK"}},
            }
        }
    spec["paths"]["/v1/golden_path"] = {
        "get": {
            "operationId": "golden_path",
            "summary": "金路径诊断（非 MCP 主工具）",
            "tags": ["foundation"],
            "parameters": [
                {
                    "name": "candidate",
                    "in": "query",
                    "schema": {"type": "string", "default": "HMPL-504"},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }
    spec["paths"]["/v1/ops"] = {
        "get": {
            "operationId": "list_ops",
            "summary": "列出 KB tools 与 Foundation ops",
            "tags": ["meta"],
            "responses": {"200": {"description": "OK"}},
        }
    }
    spec["info"]["title"] = "HMD Semantic Access Layer"
    return spec


def _jsonable(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass
    from enum import Enum

    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return value
