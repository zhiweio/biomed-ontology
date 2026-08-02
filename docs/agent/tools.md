# 11 工具与包裹链

源码：`src/biomed_ontology/agentapi/__init__.py`。

## 为什么存在这一层

底层已有 Normalizer / HybridSearcher / GraphStore。Agent 不能直接 import 它们到处调，因为：

1. **契约** —— MCP / OpenAPI 需要稳定的 Request/Response  
2. **许可闸门** —— 必须在统一出口执行，不能靠每个调用方自觉  
3. **可观测** —— 忘了埋点的工具，只会在最需要排障时缺证据  
4. **版本** —— 每个响应带 `ontology_release_id`，否则 v1/v2 答案无法争论对错  

## 工具清单（与 schema 一一对应）

| 工具 | 做什么 |
|---|---|
| `normalize_entity` | 自由文本 → CURIE，含阶段与备选 |
| `resolve_alias` | 单个别名精确解析（不做文档级 NER） |
| `expand_concept` | 概念 → 加权检索词表 |
| `get_concept` | 详情：标签、定义、父子、xref、tier |
| `search_documents` | 本体增强混合检索，可溯源片段 |
| `get_facts` | 结构化事实 + 语句级出处 |
| `sparql_query` | 受控模板 SPARQL + 可见命名图 |
| `get_landscape` | 靶点 × 适应症 × 在研药矩阵 |
| `find_analogous` | 同靶点/同机制类比资产 |
| `submit_feedback` | 以 `trace_id` 为主键的反馈 |
| `restore_context` | 碎片 → 原文语境（Citationware） |

`TOOL_SPECS` 供 MCP/OpenAPI 生成；改清单必须同步 `schema/hmd_agentapi.yaml`。README 有「11 个工具 / × 11」绊线。

## 不可绕过的包裹链

`_invoke` 是**唯一入口**：

```text
契约校验入参
  → 起 TraceContext
  → 执行工具体
  → LicenseGate
  → 契约校验出参
  → 落 ToolIoRecord
```

做成强制包裹而不是「各工具自觉调用」：遗漏埋点只会在事故调查时被发现，而那时最不能缺埋点。

## 许可闸门放哪

检索类工具在候选生成期已用 `LicenseScope`；返回体再经 `LicenseGate`，防止工具实现「手拼字典绕过 search」。还原类工具把**同一个** `permits` 谓词注入 `restore_context` —— 检索看不到却还原看得到 = 用碎片 id 换全文的后门。

## 与 search 的关系

`search_documents` 内部构造 `HybridSearcher`（或注入的实例），把 entitlements / labels / modalities 等参数原样下传。Agent 层**不**重新实现 RRF。

## 如何验证

```bash
uv run pytest tests/test_agentapi.py tests/test_service.py -q
uv run hmd demo
```
