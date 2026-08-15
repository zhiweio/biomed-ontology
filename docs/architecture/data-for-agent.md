# Data-for-Agent 契约

仓外 Agent 只依赖版本化语义接口，不直接打湖表、SPARQL 或裸向量 API。

源码：`src/biomed_ontology/foundation/context_pack.py`、`foundation/api.py`（`get_entity_context`）、`service/app.py`、`service/mcp.py`。

---

## 1. 为什么存在

检索回答「证据在哪」；推理需要一份**已治理、已声明缺失**的上下文。把两件事混成「什么都搜」，Agent 会把 `extracted` 当真理，或在后端缺失时编造字段。

本页规定 Agent **能依赖什么**，以及缺什么时必须看到 `missing[]`。

---

## 2. 设计取舍

| 决策 | 理由 | 放弃 |
|---|---|---|
| 分层数据形态 | 原文 / 证据 / 候选 / 企业知识 / 推理包职责不同 | 一个「万能 search」 |
| Context Pack 版本化 | 字段变更可观测 | 静默加字段当契约 |
| 缺后端写 `missing` | 失败要大声 | YAML fallback 或编造 |
| `extracted` 默认不进 Pack | Knowledge ≠ Truth | ingest 自动当知识 |
| 许可在候选期过滤 | 不泄漏无权资产的存在性 | 先检出再裁剪 |
| 图引擎只认 GraphDB | 语义世界单一运行时 | Neo4j / 属性图投影 |

---

## 3. 设计与实现

| 层 | 数据形态 | Agent 可调 |
|---|---|---|
| 原文 | Document（MinIO） | 不直接开放；经 `restore_context` |
| 证据 | Evidence / Tree Chunk | `search_documents` / `search_evidence` / `restore_context` |
| 候选知识 | Claim `extracted` | 默认不进推理包；需显式 `include_extracted` |
| 企业知识 | Claim `validated` + Entity | `get_entity` / `get_relationships` |
| 推理包 | Context Pack `pack_version=1.0` | `get_entity_context` |
| 资产 | OM FQN | `search_assets` |
| 反馈 | Feedback | `submit_feedback` → 演进候选 |

### 3.1 Context Pack 必带字段

`attach_pack_fields` 在既有 `get_entity_context` 载荷上挂契约字段，不删旧键：

| 字段 | 含义 |
|---|---|
| `pack_version` | 当前 `1.0` |
| `identity` | `enterprise_id` + `entity_kind` + `preferred_label_en` + `ontology_release_id` |
| `relations` | 仍以现有 `targets` / `diseases` / `drugs` / `relationships` 键暴露 |
| `evidence_tree` | 与 `evidence` 同内容，供推理消费 |
| `license` | 候选期过滤策略声明 |
| `missing[]` | 缺实体 / 证据 / 资产 / BIOS 桥时列出，不编造 |

实体未找到时仍返回 Pack：`found=false`，`missing` 含 `"entity"`。

### 3.2 推荐调用顺序

```text
resolve_entity / lookup_bios_concept
  → get_entity_context          # 有 HMD:ENT:* 时
  → search_documents / restore_context
  → submit_feedback             # 挂 source_trace_id
```

推理吃 Pack；检索吃 Evidence。不要用 `search_documents` 顶替 `get_entity_context`。

---

## 4. 不变量与失败模式

- 任一必需后端不可达 → `BackendUnavailableError`，禁止 YAML 冒充 Pack
- 公开 ID（BIOS / UMLS / HGNC…）只做 xref，不替换企业主键
- 文档不自动 mint `HMD:ENT:*`
- 仓内不做 Agent 编排 / LangGraph
- Pack 不编造缺失字段；缺什么写进 `missing[]`

---

## 5. 如何验证

```bash
uv run pytest tests/test_context_pack.py tests/test_foundation_world_model.py -q
uv run hmd serve --mcp
# POST /v1/get_entity_context  {"enterprise_id":"HMD:ENT:DC:savolitinib"}
```

相关：[Semantic Access](../tools/tools.md)、[Golden Path](../ontology/golden-path.md)、[Foundation](foundation.md)。
