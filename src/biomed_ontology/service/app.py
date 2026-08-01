"""FastAPI 应用：路由**由 TOOL_SPECS 生成**，不手写。

手写 11 条路由意味着新增工具要改三处（schema / TOOL_SPECS / 路由），
迟早漏一处，于是 `hmd contract` 导出的 OpenAPI 和真实路由对不上 ——
而 agent 团队正是照着那份 OpenAPI 接的。这里让路由表从同一份规格长出来。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from biomed_ontology.agentapi import TOOL_SPECS
from biomed_ontology.agentapi.serve import dispatch, openapi_spec
from biomed_ontology.config import Settings, settings
from biomed_ontology.service.deps import (
    ServiceState,
    build_state,
    get_state,
    parse_entitlements,
    set_state,
)

__all__ = ["create_app"]


def create_app(
    *,
    config: Settings | None = None,
    state: ServiceState | None = None,
    mcp_app: Any = None,
) -> FastAPI:
    cfg = config or settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        set_state(state or build_state(config=cfg))
        for warning in cfg.warnings():
            # 危险配置在启动时喊出来。藏在日志第 800 行等于没说。
            print(f"[WARN] {warning}")
        if mcp_app is None:
            yield
        else:
            # mount() 不会跑子应用的 lifespan，不手动串起来的话
            # MCP 的 session manager 永远不会启动，路由在但请求全挂。
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        set_state(None)

    app = FastAPI(
        title="Asliva 生物医药本体语义层",
        version="0.1.0",
        lifespan=lifespan,
    )

    for spec in TOOL_SPECS:
        _register(app, spec, cfg)

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    @app.get("/health")
    def health() -> dict[str, Any]:
        st = get_state()
        return {
            "status": "ok",
            "ontology_release_id": st.kb.release_id,
            "tools": len(TOOL_SPECS),
            "warnings": st.config.warnings(),
        }

    # OpenAPI 来自契约而非 FastAPI 反射：交付给 agent 团队的文档
    # 必须与 `hmd contract` 导出的那份逐字一致。
    app.openapi = lambda: openapi_spec()  # type: ignore[method-assign]
    return app


def _register(app: FastAPI, spec: dict[str, Any], cfg: Settings) -> None:
    tool_name = spec["name"]

    async def endpoint(
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
        x_hmd_entitlements: str | None = Header(default=None),
        x_hmd_trace_id: str | None = Header(default=None),
        x_hmd_agent_id: str | None = Header(default=None),
    ) -> JSONResponse:
        st = get_state()
        try:
            result = dispatch(
                st.api,
                tool_name,
                payload,
                entitlements=parse_entitlements(x_hmd_entitlements, config=st.config),
                agent_id=x_hmd_agent_id,
                trace_id=x_hmd_trace_id,
            )
        except TypeError as exc:
            # 参数不合法是调用方的错，422 而不是 500 —— 后者会把它记成服务故障
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(content=_jsonable(result))

    endpoint.__name__ = tool_name
    app.post(f"/v1/{tool_name}", name=tool_name, summary=spec["summary"])(endpoint)


def _jsonable(value: Any) -> Any:
    """信封里混有 dataclass、枚举、frozenset，统一压成 JSON 原生类型。"""
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
