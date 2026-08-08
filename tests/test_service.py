"""服务层：契约一致性与越权防线。

两条最要紧的性质：
1. 路由由 TOOL_SPECS + SEMANTIC_OPS 生成 → 导出的 OpenAPI 与真实路由不可能对不上；
2. `X-HMD-Entitlements` 默认不被信任 → 任何人写上 MOCK_LICENSED 都拿不到受限内容。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from biomed_ontology.config import load_settings
from biomed_ontology.foundation.api import SEMANTIC_OPS
from biomed_ontology.service.app import create_app
from biomed_ontology.service.deps import build_state, parse_entitlements
from biomed_ontology.tools import TOOL_SPECS, openapi_spec

TRUSTING = load_settings({"HMD_TRUST_ENTITLEMENT_HEADER": "true"})


@pytest.fixture(scope="module")
def state():
    return build_state()


@pytest.fixture(scope="module")
def client(state):
    with TestClient(create_app(state=state)) as c:
        yield c


@pytest.fixture(scope="module")
def trusting_client(state):
    import dataclasses

    trusted = dataclasses.replace(state, config=TRUSTING)
    with TestClient(create_app(config=TRUSTING, state=trusted)) as c:
        yield c


# ------------------------------------------------------------------ 契约一致


def test_every_declared_tool_has_a_route(client):
    """契约里写了工具却没有路由，外部调用方会照着文档接一个 404。"""
    routes = {r.path for r in client.app.routes}
    for spec in TOOL_SPECS:
        assert f"/v1/{spec['name']}" in routes
    for op in SEMANTIC_OPS:
        assert f"/v1/{op['name']}" in routes


def test_openapi_paths_match_actual_routes(client):
    """合并后的 OpenAPI 必须覆盖真实在跑的 /v1/* 路由。"""
    declared = set(client.get("/openapi.json").json()["paths"])
    actual = {r.path for r in client.app.routes if r.path.startswith("/v1/")}
    assert declared == actual


def test_openapi_comes_from_the_contract_not_framework_reflection(client):
    """FastAPI 自省出的 schema 与 LinkML 契约会漂移，必须以契约为准。"""
    spec = client.get("/openapi.json").json()
    kb_paths = set(openapi_spec()["paths"])
    assert kb_paths <= set(spec["paths"])
    for op in SEMANTIC_OPS:
        assert f"/v1/{op['name']}" in spec["paths"]


def test_health_reports_release_and_warnings(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["ontology_release_id"]
    assert body["tools"] == len(TOOL_SPECS)


# ------------------------------------------------------------------ 越权防线


def test_entitlement_header_is_ignored_by_default(client):
    """默认不信任客户端自述的凭据 —— 否则任何人都能自称买过（OWASP A01）。"""
    resp = client.post(
        "/v1/search_documents",
        json={"query": "savolitinib acquired resistance competitive landscape"},
        headers={"X-HMD-Entitlements": "MOCK_LICENSED"},
    )
    assert resp.status_code == 200
    ids = {d.get("doc_id") for d in resp.json().get("documents", [])}
    assert not any(str(i).startswith("DOC:PATSNAP") for i in ids)


def test_the_same_request_succeeds_once_the_header_is_trusted(trusting_client):
    """必须证明是"不信任"挡住了，而不是这条 query 本来就查不到东西。"""
    resp = trusting_client.post(
        "/v1/search_documents",
        json={"query": "savolitinib acquired resistance competitive landscape"},
        headers={"X-HMD-Entitlements": "MOCK_LICENSED"},
    )
    assert resp.status_code == 200
    assert resp.json()["license_tier_max"]


def test_parse_entitlements_needs_the_switch():
    assert parse_entitlements("MOCK_LICENSED") == frozenset()
    assert parse_entitlements("MOCK_LICENSED", config=TRUSTING) == {"MOCK_LICENSED"}


def test_trusting_the_header_is_announced_as_a_warning():
    assert any("凭据" in w or "entitlement" in w.lower() for w in TRUSTING.warnings())


# ------------------------------------------------------------------ 信封与错误


def test_response_carries_the_full_envelope(client):
    body = client.post("/v1/normalize_entity", json={"text": "沃利替尼"}).json()
    for key in ("trace_id", "ontology_release_id", "tool_name", "elapsed_ms"):
        assert key in body, f"信封缺字段 {key}，四支柱里的 Trace/Metrics 就断了"


def test_trace_id_header_flows_into_the_envelope(client):
    body = client.post(
        "/v1/normalize_entity",
        json={"text": "沃利替尼"},
        headers={"X-HMD-Trace-Id": "trace-from-caller"},
    ).json()
    assert body["trace_id"] == "trace-from-caller", "调用方的 trace 断了就串不起全链路"


def test_unknown_tool_is_404_not_500(client):
    assert client.post("/v1/definitely_not_a_tool", json={}).status_code == 404


def test_bad_arguments_are_422_not_500(client):
    """参数错是调用方的问题。记成 500 会把它算进服务故障率。"""
    resp = client.post("/v1/normalize_entity", json={"nonexistent_arg": 1})
    assert resp.status_code == 422


def test_tool_errors_are_warnings_not_exceptions(client):
    """工具内部错误走信封的 warnings，保持"永远返回信封"的契约。"""
    body = client.post("/v1/get_concept", json={"concept_id": "HMD:DOES.NOT.EXIST"}).json()
    assert "warnings" in body


# ------------------------------------------------------------------ MCP


def test_mcp_and_rest_share_one_dispatch():
    """两条链路若各写一遍参数处理，"MCP 上能拿到 REST 上拿不到的内容"
    就会成为最难发现的越权形态。"""
    import inspect

    from biomed_ontology.service import app as rest
    from biomed_ontology.service import mcp as mcp_mod

    assert "dispatch" in inspect.getsource(rest)
    assert "dispatch" in inspect.getsource(mcp_mod)


def test_mcp_exposes_the_same_tool_set():
    """MCP 少一个工具，换个接入方式就少一半能力，且没人会立刻发现。"""
    pytest.importorskip("fastmcp")
    import asyncio

    from biomed_ontology.service.mcp import create_mcp

    tools = asyncio.run(create_mcp().list_tools())
    expected = {s["name"] for s in TOOL_SPECS} | {o["name"] for o in SEMANTIC_OPS}
    assert {t.name for t in tools} == expected


def test_mcp_does_not_accept_client_asserted_entitlements():
    """MCP 无 HTTP 头。若从参数里读凭据，就等于把许可边界交给调用方。"""
    import inspect

    from biomed_ontology.service import mcp as mcp_mod

    source = inspect.getsource(mcp_mod)
    assert "parse_entitlements(None" in source


def test_mounted_mcp_actually_answers_over_http(state):
    """挂载正确性只能端到端验。

    最初这里全绿而真实服务是 404：`http_app()` 自带 /mcp 前缀导致路径变成
    /mcp/mcp，而且 mount() 根本不跑子应用的 lifespan，session manager 没启动。
    只测 list_tools() 两个都发现不了。
    """
    pytest.importorskip("fastmcp")
    import dataclasses

    from biomed_ontology.service.mcp import create_mcp

    mcp_app = create_mcp().http_app(path="/")
    app = create_app(state=dataclasses.replace(state), mcp_app=mcp_app)

    with TestClient(app) as c:
        resp = c.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 200, "MCP 挂载失败：路径或 lifespan 没串对"
    assert "mcp-session-id" in resp.headers, "没拿到会话 id，说明 session manager 没启动"


def test_rest_still_works_alongside_a_mounted_mcp(state):
    """挂 MCP 不能把原有 REST 路由挤掉。"""
    pytest.importorskip("fastmcp")
    import dataclasses

    from biomed_ontology.service.mcp import create_mcp

    app = create_app(state=dataclasses.replace(state), mcp_app=create_mcp().http_app(path="/"))
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"
        assert c.post("/v1/normalize_entity", json={"text": "沃利替尼"}).status_code == 200
