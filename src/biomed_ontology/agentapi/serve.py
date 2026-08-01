"""对外暴露层：MCP 工具描述符、OpenAPI 规范、统一分发入口。

不引入 FastAPI / mcp SDK 作为运行时依赖，而是产出描述符 + 一个 `dispatch`：
接入方要 MCP 就把描述符喂给 MCP server，要 HTTP 就把 OpenAPI 喂给任意框架。
把传输层绑进底座只会让"换一种接入方式"变成改底座。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from biomed_ontology.agentapi import TOOL_SPECS, AgentApi
from biomed_ontology.observability.contracts import SCHEMA_DIR

__all__ = ["dispatch", "mcp_tool_descriptors", "openapi_spec"]

_SCHEMA_FILE = SCHEMA_DIR / "hmd_agentapi.schema.json"


def _json_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))


def _class_schema(doc: dict[str, Any], name: str) -> dict[str, Any]:
    defs = doc.get("$defs") or doc.get("definitions") or {}
    return defs.get(name, {"type": "object"})


def mcp_tool_descriptors() -> list[dict[str, Any]]:
    """MCP tool 列表。inputSchema 直接取自 LinkML 生成的 JSON Schema，
    因此描述符与运行时校验用的是同一份定义，不存在文档与实现漂移。"""
    doc = _json_schema()
    return [
        {
            "name": spec["name"],
            "description": spec["summary"],
            "inputSchema": _class_schema(doc, spec["request"]),
            "outputSchema": _class_schema(doc, spec["response"]),
        }
        for spec in TOOL_SPECS
    ]


def openapi_spec(*, base_url: str = "/v1") -> dict[str, Any]:
    doc = _json_schema()
    defs = doc.get("$defs") or doc.get("definitions") or {}
    paths = {}
    for spec in TOOL_SPECS:
        paths[f"{base_url}/{spec['name']}"] = {
            "post": {
                "operationId": spec["name"],
                "summary": spec["summary"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{spec['request']}"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{spec['response']}"}
                            }
                        },
                    }
                },
                "parameters": [
                    {
                        "name": "X-HMD-Entitlements",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "逗号分隔的已采购源 ID。缺省视为仅可见 TIER_0/1。",
                    },
                    {
                        "name": "X-HMD-Trace-Id",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "上游 trace_id。透传后可把 agent 侧与底座侧的链路拼成一条。",
                    },
                ],
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "HMD Biomed Ontology Agent API", "version": "0.1.0"},
        "paths": paths,
        "components": {"schemas": defs},
    }


# 请求字段 → 方法参数的映射。schema 用契约命名，Python 侧用调用者习惯的命名，
# 两边不强行统一，靠这张表衔接。
_ARG_MAP: dict[str, dict[str, str]] = {
    "search_documents": {"use_expansion": "expand"},
    "sparql_query": {"sparql_template": "template"},
    "submit_feedback": {"trace_id": "source_trace_id"},
}


def dispatch(
    api: AgentApi,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    entitlements: frozenset[str] = frozenset(),
    agent_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """MCP / HTTP 的共同入口。所有工具都从这里走，包裹链因此无法被绕过。"""
    handler = getattr(api, tool_name, None)
    if handler is None or not any(s["name"] == tool_name for s in TOOL_SPECS):
        raise ValueError(f"未注册的工具：{tool_name}")

    mapping = _ARG_MAP.get(tool_name, {})
    kwargs = {mapping.get(k, k): v for k, v in arguments.items()}
    if tool_name == "sparql_query":
        raw = kwargs.pop("bindings", None) or []
        if isinstance(raw, list):
            kwargs["bindings"] = dict(b.split("=", 1) for b in raw if "=" in b)
    kwargs.update(entitlements=entitlements, agent_id=agent_id, trace_id=trace_id)

    positional = {
        "normalize_entity": "text",
        "resolve_alias": "alias",
        "expand_concept": "concept_id",
        "get_concept": "concept_id",
        "search_documents": "query",
        "find_analogous": "concept_id",
        "submit_feedback": "verdict",
        "sparql_query": "template",
    }.get(tool_name)
    if positional and positional in kwargs:
        return handler(kwargs.pop(positional), **kwargs)
    return handler(**kwargs)


def write_contract_bundle(out_dir: Path) -> list[Path]:
    """把 agent 团队需要的全部契约物料落到一个目录，方便直接交付。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in (
        ("mcp_tools.json", mcp_tool_descriptors()),
        ("openapi.json", openapi_spec()),
    ):
        p = out_dir / name
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(p)
    return written
