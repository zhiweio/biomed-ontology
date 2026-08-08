# Semantic Tool API

源码：`src/biomed_ontology/tools/api.py`。

本仓库**不做 Agent 编排**。对外只提供可被仓外 LLM / coding agent（GPT、Codex 等）调用的检索与查询工具。

## KB 工具（LinkML 契约）

| 工具 | 作用 |
|---|---|
| `normalize_entity` | 自由文本 → 概念 code（含歧义备选） |
| `resolve_alias` | 单个别名精确解析（不做文档级 NER） |
| `expand_concept` | 同义词 + 下位词加权扩展 |
| `get_concept` | 概念详情 / 父子 / 许可等级 |
| `search_documents` | 本体增强混合检索 + Citationware 证据树 |
| `get_facts` | 结构化事实 + 语句级出处 |
| `submit_feedback` | data loop：回写判定 → 演进信号 |
| `restore_context` | 碎片 → 原文（章节 / 页码 / 许可） |

`TOOL_SPECS` 供 MCP/OpenAPI 生成；改清单必须同步 `schema/hmd_tools.yaml`。README 有工具数绊线。

## Foundation Semantic Ops

与 KB 工具挂在同一 `hmd serve`（REST + MCP）：

| 工具 | 作用 |
|---|---|
| `resolve_entity` | 文本 → Enterprise ID |
| `get_entity` / `get_relationships` / `find_related_entities` | GraphDB |
| `search_evidence` / `get_entity_evidence` | Milvus Evidence Index |
| `search_assets` / `get_entity_assets` | OpenMetadata |
| `get_entity_context` | 聚合上下文（禁止 YAML fallback） |

`golden_path` 仅 REST/CLI 诊断，不进 MCP 主工具表。

## 已退役（不再对外暴露）

- `sparql_query` — 禁止裸 SPARQL 作主契约
- `get_landscape` / `find_analogous` — 分析编排型，非检索基座

## 如何验证

```bash
uv run pytest tests/test_tools.py tests/test_service.py tests/test_eval_demo.py -q
uv run hmd demo              # Rich：Trace + 分场景面板（对齐 foundation golden）
uv run hmd demo --compact    # 仅 Trace
uv run hmd demo --json       # 机器可读
uv run hmd serve --mcp
```
