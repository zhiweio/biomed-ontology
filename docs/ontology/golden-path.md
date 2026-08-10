# Golden Path

第一条可演示、可机检的 Enterprise World Model 链路。原则：**不要一次做 100 个 class**——先把一条链跑通，再扩展 TBox。

种子实体：**HMPL-504 / savolitinib**（`HMD:ENT:DC:savolitinib`）。文档中的示意代号 `ABC-001` 映射到该实体。

样例包：

- 有 ENT：[`ontology/examples/golden_path/hmpl504/`](https://github.com/zhiweio/biomed-ontology/tree/main/ontology/examples/golden_path/hmpl504)
- 无 ENT / 公开 CURIE：[`ontology/examples/golden_path/public_no_ent/`](https://github.com/zhiweio/biomed-ontology/tree/main/ontology/examples/golden_path/public_no_ent)（`hmd demo W3`、`hmd eval --suite public_bios`）

---

## 1. 为什么存在

PoC 最容易死在「每个模块都能 demo，但没有一条端到端故事」。Golden Path 定义：

- **用户问题**可陈述
- **策展数据**可版本化
- **Semantic Ops**可聚合回答
- **评测**可检查三后端（GraphDB / Milvus / OpenMetadata）而非 YAML fallback

它是 `ontology:validate`、`hmd foundation golden`、`golden-eval` 的共同锚点。

---

## 2. 设计取舍

| 决策 | 理由 |
|---|---|
| 单候选药纵向切片 | 降低策展与评测成本 |
| Citationware 第一版即成型 | Claim + span + evidence_ids |
| 运行时禁 YAML fallback | 强迫三后端联调真实 |
| PubMed + Patent + ELN 三源证据 | 覆盖外部与内部数据面 |
| `get_entity_context` 为主契约 | 不暴露裸 SPARQL / 向量 API |

---

## 3. 设计与实现

### 3.1 链路总览

```text
候选药物 → Target → Disease → Evidence → 企业 Asset
```

```text
                    仓外 LLM / Tool Client
                            │
                            ▼
                 get_entity_context()
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
           GraphDB        Milvus      OpenMetadata
          World Model    Evidence      Assets
              │             │             │
              └──────┬──────┘             │
                     ▼                    │
              HMPL-504 / savolitinib ◄────┘
                     │
                  targets
                     ▼
                    MET
                     │
              associatedWith
                     ▼
                  NSCLC
                     │
         PubMed / Patent / ELN / LIMS
```

### 3.2 用户问题

> Candidate X 的作用靶点是什么？它主要针对哪些疾病？有哪些 PubMed / Patent 证据？企业内部有哪些 ELN / LIMS 数据支持这些结论？

### 3.3 步骤与数据锚点

| 步骤 | 内容 | 锚点 |
|---|---|---|
| 1 定义候选药 | DrugCandidate + exactMatch | `HMD:ENT:DC:savolitinib` → BIOS/ChEBI/DrugBank |
| 2 识别 | BERN2 / 词典 | `HMPL-504` / `MET` / `NSCLC` → `EntityResolver` |
| 3 Target 关系 | `targets` | `HMD:ENT:TGT:MET` |
| 4 Disease 关系 | `indication` / `associatedWith` | `HMD:ENT:IND:nsclc` |
| 5 PubMed Evidence | Claim + span | `pubmed:` / `ev:lit:*` |
| 6 Patent Evidence | 同 pipeline | `patent:` / `ev:pat:*` |
| 7 ELN / LIMS | Experiment / Assay | `testedIn`、`hasAssay` + OM 资产 |

策展文件：

- `ontology/entities/enterprise_entities.yaml`
- `ontology/claims/`（validated claims）
- `data/foundation/` 样例 evidence / assets（经 sync 投影）

### 3.4 Citationware 形状

```text
Claim
 ├── subject / predicate / object
 ├── evidence_ids
 ├── span
 ├── claim_status (extracted | validated)
 └── confidence
```

`get_entity_context` 聚合返回：`entity` / `targets` / `diseases` / `evidence` / `internal_assets`。

实现：`foundation/api.py` 的 `get_entity_context`；存储：`foundation/store.py` + Milvus + `OpenMetadataClient`。

### 3.5 Semantic Access 入口

运行时强制读 **GraphDB + Milvus + OpenMetadata**。策展 YAML 仅离线 → `ontology:validate` → `hmd foundation sync`（幂等）入库。

```bash
uv run hmd foundation sync
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation golden --candidate HMPL-504 --json
uv run hmd foundation golden-eval
uv run hmd foundation golden-eval --compact
uv run hmd foundation golden-eval --json
uv run hmd serve --mcp
```

文献面检索（Milvus 五列 + 图通道）经 `open_dual_surface()` 的 `ToolApi`，与 Foundation 面并列暴露。

### 3.6 核心类（第一版）

DrugCandidate、Target、Indication、Program、Experiment、Assay、Publication、Evidence（claim）、以及必要的 Compound / Biomarker 槽位。定义见 `schema/hmd_enterprise.yaml`。

### 3.7 评测检查项

`golden-eval` 验证：

- `backends` 无 yaml fallback
- BIOS 桥接读 GraphDB `graph/biomedical`
- 证据来自 Milvus `foundation_evidence`
- 资产来自 OpenMetadata Glossary

观测：检索操作 structlog 四支柱 `Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)`。

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| HMPL-504 必须 resolve 到 `HMD:ENT:DC:savolitinib` | S1 Identity eval |
| validated claim 才进 knowledge 边 | extracted 仅 provenance |
| 三后端齐 | 任一不可用 → `BackendUnavailableError` |
| evidence 含 quote/span | 非仅 PMID 列表 |
| 资产 FQN 锚 ENT | OM term 关联企业 ID |

| 失败模式 | 排查 |
|---|---|
| golden 读 YAML | sync 未跑或 API fallback 被打开 |
| 无 MET 边 | entities YAML 或 sync TTL |
| Milvus 无证据 | `foundation sync` / ingest |
| BIOS 桥接空 | `hmd foundation bios-load` |

---

## 5. 如何验证

```bash
task foundation:up
uv run hmd foundation sync
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation golden-eval --compact
uv run pytest tests/test_foundation_world_model.py -q
uv run pytest tests/test_eval_targets.py -q
```

MCP / REST 主契约：`get_entity_context`。完整命令与环境见 [快速开始](../getting-started.md)、[Foundation](../architecture/foundation.md)。
