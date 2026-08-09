# REST / MCP 服务契约

源码：`src/biomed_ontology/service/`（`app.py`、`deps.py`、`dispatch.py`、`mcp.py`）。  
清单源：`TOOL_SPECS`（`tools/api.py`，8 个 KB 工具）+ `SEMANTIC_OPS`（`foundation/api.py`，9 个 Foundation ops）；运行时以 `GET /v1/ops` 为准。

## 为什么存在

仓外调用方需要**单一 HTTP 入口**同时获得 Ontology Semantic Layer 与 Foundation World Model，且 REST 与 MCP 必须共享同一套 KB、许可闸门与观测 hub。若两套入口各自装配，会出现「REST 过闸门、MCP 不过」或 `feedback_log` 分叉，演进闭环与合规审计都会断裂。

`hmd serve` 因此是唯一对外服务进程：FastAPI + 可选 FastMCP Streamable HTTP，经 `build_state()` → `open_dual_surface()` 装配 `ToolApi` + `FoundationApi`。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 入口数量 | 单进程 `hmd serve` | 独立的 `foundation serve` |
| MCP 工具数 | 17（= 8 KB + 9 Foundation） | 把 `golden_path` 塞进 MCP |
| 凭据 | 默认不信任 `X-HMD-Entitlements` | 客户端自报订阅源 |
| KB 契约 | LinkML 请求类 + `dispatch` 校验 | 各路由手写 body 模型 |
| Foundation 失败 | `BackendUnavailableError` 明确报错 | seed YAML 冒充三后端 |
| 契约导出 | `hmd contract` 生成 OpenAPI + MCP 描述符 | 手工维护两份清单 |

## 设计与实现

### 唯一入口

```bash
uv run hmd serve --mcp          # 默认 :8000
```

同一进程暴露 **Semantic Access**（世界模型访问面）：

- **Ontology Semantic Layer**（KB tools：术语 / 扩展 / 事实 / 检索 / Citationware / feedback）
- **Foundation Semantic Ops**（GraphDB / Milvus Evidence / OpenMetadata）
- **MCP**：`POST /mcp/`（需 `--mcp`）

`build_state()` 调用 `open_dual_surface(load_literature=True)`，文献检索要求 Milvus 集合已建。`/v1/golden_path` 会带上文献腿（`FoundationApi.golden_path(..., tools=...)`）。

能力群对照见 [tools](tools.md)。

### 进程状态（`ServiceState`）

| 字段 | 作用 |
|---|---|
| `api` | `ToolApi` 单例（含 `feedback_log`、`searcher`） |
| `foundation` | `FoundationApi`（可 `load_foundation=False` 关闭） |
| `kb` | 文献 `KnowledgeBase`（含 `hub`） |
| `config` | `Settings`（凭据信任开关等） |

`feedback_log` 与 `hub` **必须跨请求共享**，否则演进信号无法挖掘。

### 请求路径

```text
HTTP POST /v1/{tool_name}
    → dispatch(state, tool_name, body, headers)
        → parse_entitlements(X-HMD-Entitlements)  [默认空集]
        → ToolApi._invoke / FoundationApi.{op}
        → 契约校验 + 许可过滤 + ObservabilityHub

MCP tool call
    → 同一 dispatch（KB：arguments 对象；Foundation：展开字段）
    → parse_entitlements(None)  # MCP 不接受客户端自称凭据
```

请求头（KB 路由）：

| 头 | 含义 |
|---|---|
| `X-HMD-Client-Id` | 调用方身份（旧别名 `X-HMD-Agent-Id` 仍接受） |
| `X-HMD-Trace-Id` | 可选；未传则服务端生成 |
| `X-HMD-Entitlements` | 采购凭据（逗号分隔源 ID）；**默认不信任** |

### REST 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查（release / 工具数 / foundation ops / mcp 开关） |
| `GET` | `/v1/ops` | 返回当前 `kb_tools` + `foundation_ops` 清单 |
| `GET` | `/docs` | FastAPI 交互文档 |
| `GET` | `/openapi.json` | OpenAPI（KB 以 LinkML 为准，合并 Foundation 路径） |
| `POST` | `/mcp/` | MCP Streamable HTTP（需 `--mcp`） |

#### KB tools（`POST /v1/{name}`）

经 `dispatch`：契约校验 / 许可过滤 / 观测。Body 以 LinkML 请求类为准。

| 路径 | 说明 | 主要参数 |
|---|---|---|
| `POST /v1/normalize_entity` | 自由文本 → 唯一 code，返回归一化阶段与备选义项 | `text`；可选 `entity_types` / `context` / `top_k` / `allow_llm` / `detect_spans` |
| `POST /v1/resolve_alias` | 单个别名的精确解析，不做文档级 NER | 同 `NormalizeRequest` |
| `POST /v1/expand_concept` | 概念 → 加权检索词表（同义词 + 下位词） | `concept_id`；可选 `max_depth` / `include_descendants` / `min_weight` / `top_k` |
| `POST /v1/get_concept` | 概念详情：标签、定义、父子、外部映射、许可等级 | 同 `ExpandRequest` |
| `POST /v1/search_documents` | 本体增强混合检索，返回带 section/page 的可溯源片段 | `query`；可选 `top_k` / `entity_types` / `doc_types` / `labels` / `channels` / `modalities` |
| `POST /v1/get_facts` | 结构化事实 + 语句级出处 | 可选 `subject_id` / `predicate` / `object_id` / `min_confidence` / `include_evidence` |
| `POST /v1/submit_feedback` | 回写判定结果，驱动本体演进闭环 | `trace_id` + `verdict`；可选 `reason` / `expected_concept_id` |
| `POST /v1/restore_context` | 碎片 → 原文：章节全文、面包屑与原始页码 | `chunk_id`；可选 `restore_scope` / `max_chars` |

#### Foundation Semantic Ops

后端不可达时明确报错，**无 YAML fallback**。主契约：`get_entity_context`。

| 路径 | 说明 | Body |
|---|---|---|
| `POST /v1/resolve_entity` | 文本/别名 → Enterprise Entity ID（词典 / BERN2 候选 + Resolver） | `{ "text", "type_hint"? }` |
| `POST /v1/get_entity` | 按 Enterprise ID 取实体（GraphDB） | `{ "enterprise_id" }` |
| `POST /v1/get_relationships` | KnowledgeClaim（GraphDB provenance） | `{ "enterprise_id", "predicate"? }` |
| `POST /v1/find_related_entities` | 一跳相关企业实体（GraphDB） | `{ "enterprise_id" }` |
| `POST /v1/search_evidence` | Evidence Index（Milvus） | `{ "query"?, "entity_ids"?, "require_quote"? }` |
| `POST /v1/search_assets` | 企业资产（OpenMetadata Glossary） | `{ "query"?, "entity_ids"? }` |
| `POST /v1/get_entity_evidence` | 实体 → 证据（Milvus） | `{ "enterprise_id" }` |
| `POST /v1/get_entity_assets` | 实体 → 资产（OpenMetadata） | `{ "enterprise_id" }` |
| `POST /v1/get_entity_context` | 聚合：GraphDB + Milvus + OpenMetadata | `{ "enterprise_id" }` |
| `GET /v1/golden_path` | 金路径诊断（含文献腿）；**仅 REST/CLI，不进 MCP** | query：`candidate`（默认 `HMPL-504`） |

示例：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/ops | jq .
curl -s -X POST http://127.0.0.1:8000/v1/normalize_entity \
  -H 'content-type: application/json' \
  -d '{"text":"savolitinib"}'
curl -s -X POST http://127.0.0.1:8000/v1/get_entity_context \
  -H 'content-type: application/json' \
  -d '{"enterprise_id":"HMD:ENT:…"}'
```

### MCP tools

挂载点：`/mcp`（FastMCP Streamable HTTP）。工具名与 REST 同名，**同一套** `TOOL_SPECS` + `SEMANTIC_OPS`（共 **17** 个）；`golden_path` 不暴露。

KB 工具参数为单个 `arguments` 对象（与 REST JSON body 同形），走同一 `dispatch`。  
Foundation 工具按 op 展开具名参数（与 REST body 字段对齐）。MCP **不接受**客户端自称凭据（`parse_entitlements(None)`）。

#### KB（8）

| 工具名 | 说明 |
|---|---|
| `normalize_entity` | 自由文本 → 唯一 code，返回归一化阶段与备选义项 |
| `resolve_alias` | 单个别名的精确解析，不做文档级 NER |
| `expand_concept` | 概念 → 加权检索词表（同义词 + 下位词） |
| `get_concept` | 概念详情：标签、定义、父子、外部映射、许可等级 |
| `search_documents` | 本体增强混合检索，返回带 section/page 的可溯源片段 |
| `get_facts` | 结构化事实 + 语句级出处 |
| `submit_feedback` | 回写判定结果，驱动本体演进闭环 |
| `restore_context` | 碎片 → 原文：还原所在章节全文、面包屑与原始页码 |

#### Foundation（9）

| 工具名 | 说明 | 参数 |
|---|---|---|
| `resolve_entity` | 文本/别名 → Enterprise Entity ID | `text`, `type_hint?` |
| `get_entity` | 按 Enterprise ID 取实体（GraphDB） | `enterprise_id` |
| `get_relationships` | KnowledgeClaim（GraphDB provenance） | `enterprise_id`, `predicate?` |
| `find_related_entities` | 一跳相关企业实体（GraphDB） | `enterprise_id` |
| `search_evidence` | Evidence Index（Milvus） | `query?`, `entity_ids?`, `require_quote?` |
| `search_assets` | 企业资产（OpenMetadata Glossary） | `query?`, `entity_ids?` |
| `get_entity_evidence` | 实体 → 证据（Milvus） | `enterprise_id` |
| `get_entity_assets` | 实体 → 资产（OpenMetadata） | `enterprise_id` |
| `get_entity_context` | 聚合：GraphDB + Milvus + OpenMetadata（禁止 YAML fallback） | `enterprise_id` |

### 凭据

`X-HMD-Entitlements` **默认不被信任**（`HMD_TRUST_ENTITLEMENT_HEADER=false`）。  
生产环境由网关按已认证身份注入。

调用方身份头：`X-HMD-Client-Id`（旧 `X-HMD-Agent-Id` 仍作别名接受）。

### 契约导出

```bash
uv run hmd contract --out build/contract
```

产出 MCP 描述符与 OpenAPI（KB 工具以 LinkML 为准；Foundation ops 合并进同一 OpenAPI）。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| REST / MCP 同一 `ServiceState` | 反馈与观测分叉 |
| MCP 工具数 = 17 | README / health 绊线失败 |
| 文献面依赖 Milvus | 服务启动或 `open_dual_surface` 失败 |
| MCP 无 entitlements 头 | 仅能看 TIER_1 及以下开放内容 |
| `golden_path` 不进 MCP | 避免与生产工具混淆 |
| Foundation 后端 down → 硬错误 | 防止 YAML 假数据进入联调结论 |

常见失败：

- **503 / BackendUnavailableError**：`task foundation:up` 后 `hmd foundation sync`。
- **Milvus 集合不存在**：`hmd index --recreate`。
- **契约校验失败**：body 与 LinkML 不一致；查 `/openapi.json`。
- **LICENSE_DENIED**：凭据不足；生产检查网关注入。

## 如何验证

```bash
uv run pytest tests/test_service.py tests/test_tools.py -q
uv run hmd serve --mcp &
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/ops | jq '.kb_tools | length, .foundation_ops | length'
uv run hmd contract --out build/contract
```

预期：`kb_tools` = 8，`foundation_ops` = 9；health 中 mcp 开关与 `--mcp` 一致。
