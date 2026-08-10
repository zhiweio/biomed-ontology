# Semantic Access（工具契约）

源码：`src/biomed_ontology/tools/api.py`（`ToolApi`）、`src/biomed_ontology/foundation/api.py`（`FoundationApi`）。  
契约 SSOT：`schema/hmd_tools.yaml`（KB）、`TOOL_SPECS` / `SEMANTIC_OPS`（运行时清单）。

## 为什么存在

本仓库**不做 Agent 编排**。`hmd serve` 把 Ontology Semantic Layer 与 Foundation 世界模型以 **MCP / REST 契约**暴露给仓外调用方——不是「检索工具箱」产品页，而是**世界模型的访问面**。

调用方（Agent、内部服务、评测脚本）需要一套稳定、可契约校验、可许可过滤、可观测的语义 API，而不是直接碰 KB 内部对象或 GraphDB SPARQL。Semantic Access 把「术语归一 → 概念扩展 → 混合检索 → 引用还原 → 反馈回写」与「企业实体解析 → 公开 BIOS 查阅 → 关系遍历 → 证据索引 → 资产聚合」收敛到 **18** 个具名操作（KB 8 + Foundation 10），由 `dispatch` / Foundation 路由统一走契约校验、许可闸门与四支柱观测。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 契约形态 | LinkML 生成请求/响应类 + `TOOL_SPECS` 清单 | 手写 OpenAPI 与实现分叉 |
| 世界模型面 | `FoundationApi` 独立 10 ops，后端不可达时硬失败 | YAML 回落冒充 GraphDB / Milvus |
| 文献检索 | 经 `HybridSearcher`，Milvus 为生产后端；无 ENT 时默认公开别名改写 BM25/DENSE | 内存词法「悄悄顶替」Milvus；公开概念进 GRAPH 种子 |
| Citationware | 检索响应内嵌 `evidence_tree` + 独立 `restore_context` | 只返回扁平 snippet、无还原路径 |
| 编排型分析 | 不暴露 | `get_landscape` / `find_analogous` 等分析编排 |
| 裸 SPARQL | 不对外 | `sparql_query` 作主契约 |
| 诊断入口 | `golden_path` 仅 REST/CLI | 不进 MCP 主工具表（避免与 18 工具混淆） |
| 公开 BIOS | `lookup_bios_concept`（无需 ENT） | 用 `get_entity` 接受 BIOS URI |

改 `TOOL_SPECS` 或 `SEMANTIC_OPS` 必须同步 `schema/hmd_tools.yaml` 与 README 工具数绊线。

## 设计与实现

### 双面装配

运行时经 `open_dual_surface()`（`runtime.py`）装配：

- **文献面**：`ToolApi.from_backends(kb, searcher, foundation=…)` — 要求 Milvus 集合已建（或测试注入 `milvus_backend` / `searcher`）。
- **世界模型面**：`FoundationApi(world)` — GraphDB / Milvus Evidence / OpenMetadata 由各自 client 健康检查决定可用性。

`hmd serve` 的 `build_state()` 在同一进程内调用 `open_dual_surface()`，保证 REST 与 MCP 共享同一 `ToolApi` 与 `FoundationApi` 实例（含跨请求 `feedback_log` 与 `hub`）。

### 能力群 → 工具

| 能力群 | 数据面 | 工具 | 说明 |
|---|---|---|---|
| 术语与身份 | KB | `normalize_entity`、`resolve_alias` | 自由文本 / 单别名 → 概念 code；`resolve_alias` 不做文档级 NER |
| 层级与扩展 | KB | `expand_concept`、`get_concept` | 加权检索词表、概念详情与许可 tier |
| 结构化事实 | KB | `get_facts` | 三元组 + 语句级 evidence |
| 证据检索 | KB | `search_documents` | 本体增强混合检索；无 ENT 时默认 PublicLexicalExpand；可传 `expansion_terms`；响应含 `expansion_source` / `evidence_tree` |
| Citationware | KB | `evidence_tree`（检索内嵌）、`restore_context` | 碎片 → 章节全文；见 [citationware](citationware.md) |
| 演进信号 | KB | `submit_feedback` | 以**被评价调用**的 `trace_id` 为主键回写 |
| 企业身份 | Foundation | `resolve_entity` | 词典 / BERN2 + Resolver；无 ENT 时附 `search_surfaces` |
| 公开 BIOS | Foundation | `lookup_bios_concept` | 无需 ENT；卡片 + 别名邻域 + 可选企业桥；可接 `search_documents` |
| 关系遍历 | Foundation | `get_entity`、`get_relationships`、`find_related_entities` | GraphDB；claim 带 provenance |
| Evidence Index | Foundation | `search_evidence`、`get_entity_evidence` | Milvus |
| 企业资产 | Foundation | `search_assets`、`get_entity_assets` | OpenMetadata Glossary |
| 聚合上下文 | Foundation | `get_entity_context` | GraphDB + Milvus + OM 聚合；**禁止 YAML fallback**（需 ENT） |

策展 YAML / 映射文件分别被哪些 op 消费（`entities`→`get_entity`、`dictionary`→`resolve_entity`、`catalog`→`normalize_entity`…）：见 [策展资产与运行时机制 · 资产→工具矩阵](../ontology/curation-and-runtime.md#35-rest-mcp)。

### ToolApi 调用链

所有 KB 工具经 `_invoke` 包裹：

1. 分配 `trace_id`，写入 `TraceContext`（含 `ontology_release_id`）。
2. 契约校验（`ContractValidator`）。
3. 执行业务 handler（检索、归一化等），决策点 `record_decision`。
4. 许可过滤与 `LicenseScope` 统计（`license_filtered_count`）。
5. 落 `ToolIoRecord` 到 `ObservabilityHub`。

返回体携带 `trace_id`，供 `submit_feedback` 与演进挖掘挂接。

### FoundationApi 契约要点

- `resolve_entity`：词典 / BERN2 做 ER，**不**把 seed YAML 当作 World Model 查询回落；`bern2_candidate` 附 `search_surfaces`（BIOS pref/alt）。
- `lookup_bios_concept`：公开概念只读卡；不 mint ENT；邻居 v1 = 别名 / 同 xref / 企业 exact 桥。
- `search_documents`：有 ENT → rewrite+GRAPH；无 ENT → 默认公开别名改写（`HMD_PUBLIC_LEXICAL_EXPAND`，默认 true）。
- `get_entity_context`：三后端聚合的主契约（需 ENT）；任一必需后端不可达 → `BackendUnavailableError`。

推荐 Agent 组合：

```text
resolve_entity / lookup_bios_concept → search_documents([expansion_terms]) → restore_context
# 有 HMD:ENT:* 时再:
get_entity_context / expand_concept / get_relationships
```
- `golden_path`：诊断用金路径（含文献腿），由 CLI `hmd foundation golden` / REST `GET /v1/golden_path` 暴露，不在 MCP 17 工具内。

### 已退役（不再对外暴露）

| 工具 | 原因 |
|---|---|
| `sparql_query` | 禁止裸 SPARQL 作主契约；图访问经具名 ops |
| `get_landscape` | 分析编排型，不属于 Semantic Access |
| `find_analogous` | 同上 |

## 不变量与失败模式

| 不变量 | 违反时的表现 |
|---|---|
| 工具清单与 LinkML 一致 | OpenAPI / MCP 生成漂移；契约测试红灯 |
| 文献检索必须 Milvus（生产） | `open_dual_surface` 抛 `RuntimeError`（集合不存在或不可达） |
| 许可过滤在候选生成期生效 | 无权内容不进结果集，统计量不泄漏存在 |
| `restore_context` 复用 `LicenseScope.permits` | 检索看不到的内容，还原也不得看到 |
| `submit_feedback` 挂 `source_trace_id` | 反馈无法回放当时候选集 |
| Foundation 无 YAML fallback | 联调栈未起时明确报错，而非返回 seed 快照 |
| `citation_fidelity`（评测）= 1.0 | 任臂 < 1.0 视为造引用，T5 硬失败 |

常见失败：

- **Milvus 未索引**：`hmd serve` / `open_dual_surface` 启动失败 → 先 `task milvus:up` + `hmd index --recreate`。
- **GraphDB 未 sync**：含 `require_graph` 的检索臂标「未运行」；`expand_concept` 对 `HMD:ENT:*` 可回落 catalog。
- **凭据越权**：`LICENSE_DENIED` / `LicenseViolation`，不做 tier 降级返回。

## 如何验证

```bash
uv run pytest tests/test_tools.py tests/test_service.py tests/test_runtime_dual_surface.py -q
uv run hmd demo              # Rich：World Model / 语义层能力验收
uv run hmd demo --compact    # 仅 Trace
uv run hmd demo --json       # 机器可读
uv run hmd serve --mcp
uv run hmd contract --out build/contract
```

HTTP / MCP 契约细节见 [serve](serve.md)；Citationware 见 [citationware](citationware.md)。
