# Semantic Access（Foundation Semantic API）

源码：`src/biomed_ontology/tools/api.py`、`src/biomed_ontology/foundation/api.py`。

本仓库**不做 Agent 编排**。`hmd serve` 把 Ontology Semantic Layer 与 Foundation 世界模型
以 MCP/REST 契约暴露给仓外调用方——不是「检索工具箱」产品页，而是世界模型的访问面。

## 能力群 → 工具

| 能力群 | KB / Foundation | 工具 |
|---|---|---|
| 术语与身份 | KB | `normalize_entity`、`resolve_alias` |
| 层级与扩展 | KB | `expand_concept`、`get_concept` |
| 结构化事实 | KB | `get_facts` |
| 证据检索 | KB | `search_documents` |
| Citationware | KB | 检索响应中的 evidence_tree；`restore_context` |
| 演进信号 | KB | `submit_feedback` |
| 企业身份 | Foundation | `resolve_entity` |
| 关系遍历 | Foundation | `get_entity`、`get_relationships`、`find_related_entities` |
| Evidence Index | Foundation | `search_evidence`、`get_entity_evidence` |
| 企业资产 | Foundation | `search_assets`、`get_entity_assets` |
| 聚合上下文 | Foundation | `get_entity_context`（禁止 YAML fallback） |

`TOOL_SPECS` 供 MCP/OpenAPI 生成；改清单必须同步 `schema/hmd_tools.yaml`。README 有工具数绊线。  
`golden_path` 仅 REST/CLI 诊断，不进 MCP 主工具表。

## 已退役（不再对外暴露）

- `sparql_query` — 禁止裸 SPARQL 作主契约
- `get_landscape` / `find_analogous` — 分析编排型，不属于 Semantic Access 主契约

## 如何验证

```bash
uv run pytest tests/test_tools.py tests/test_service.py tests/test_eval_demo.py -q
uv run hmd demo              # Rich：World Model / 语义层能力验收
uv run hmd demo --compact    # 仅 Trace
uv run hmd demo --json       # 机器可读
uv run hmd serve --mcp
```
