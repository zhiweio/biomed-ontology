# Enterprise Biomedical World Model（Foundation）

面向创新药研发的 **Enterprise Biomedical World Model / AI Data Foundation**：
以 **Enterprise Ontology**（`HMD:ENT:*`）为锚，把公共生物医学概念、企业关系、可引用证据与
ELN/LIMS 资产连成可查询、可追溯的语义世界，经 `hmd serve`（MCP/REST）供仓外 Agent 使用。

> **BIOS provides the biomedical world. Enterprise Ontology provides the company's world.**

`hmd serve` 同时暴露 KB 侧 Ontology Semantic Layer（术语 / 层级 / 事实 / 检索 / Citationware…）
与 Foundation Semantic Ops（实体 / 关系 / 证据 / 资产 / `get_entity_context`）。
别名与检索只是语义层中的两项，不是产品定义。

## 非目标

- 不做 LangGraph / vLLM / 多步 Agent 编排 / 报告自动生成
- 不做自研 RDF 引擎、Ontology Editor、ER 引擎、向量库、Catalog、Agent Runtime
- 仓外调用方 **不得**把裸 `graph_sparql` / 原始向量 API 当主契约；应调用 Semantic Ops

## 核心判断

| 角色 | 是什么 | 不是什么 |
|---|---|---|
| **Enterprise Ontology** | 世界模型核心（DrugCandidate、Program、Assay…） | 不是 BIOS 子集 |
| **BIOS_v3** | 公共生物医学知识源（External Concepts） | 不是企业主键 owner |
| **BERN2** | Recognition + Candidate Normalization（NLU） | 不是通用 Entity Resolution |
| **Entity Resolution** | 企业身份解析 → `HMD:ENT:*` | 不是「BERN2 直出 BIOS URI」 |
| **GraphDB** | World Model 运行时（语义 / 关系 / 溯源） | Free ≠ 生产架构决策 |
| **Milvus** | **Evidence Index**（证据在哪） | 不是普通「向量库」话术 |
| **OpenMetadata** | **Enterprise Data Context**（资产在哪） | 不是仅 Glossary 打 BIOS 标签 |

## 运行时栈

```text
External Agents
      │ MCP / REST
      ▼
Semantic Access (hmd serve)
  KB tools + Foundation ops
      ├─► GraphDB        关系 / 本体 / PROV
      ├─► Milvus         Evidence Index
      └─► OpenMetadata   Data Context
            ▲
   Enterprise Ontology + LinkML / SHACL / PROV
            ▲
   BIOS (xref) · BERN2 · Dict · Zingg → HMD:ENT:*
```

三层职责对照：

| Layer | 核心问题 |
|---|---|
| GraphDB | What is related to what? |
| Milvus | Where is the evidence? / Why believe? |
| OpenMetadata | Where is the enterprise data? |

## 三层 ID（禁止 BIOS 作企业主键）

```text
              Enterprise Ontology ID  ← 对外语义锚点 / 主键
                     │
        ┌────────────┼────────────────────┐
        │            │                    │
  Enterprise     External Concept      Evidence
  HMD:ENT:DC:…   BIOS:…                pubmed:…
  HMD:ENT:PRG:…  ChEBI:… / HGNC:…      patent:…
  HMD:ENT:EXP:…  DrugBank:…            eln:… / lims:… / ev:…
                 （skos:exactMatch）
```

- Milvus `entity_ids`、API `canonical_entity`、OM 资产关联：**优先 Enterprise ID**
- BIOS URI：外部 biomedical concept identity，经映射挂接
- Evidence ID：锚定出处与 Evidence Index 条目；**不是**企业实体主键
- SSOT：`schema/hmd_enterprise.yaml` → `task gen` → `src/biomed_ontology/_generated/hmd_enterprise.py`
- 工具链职责：[Ontology Toolchain](../ontology/toolchain.md)（LinkML SSOT；Protégé 只审阅；**不引入 Jena**）

## Entity Resolution

```text
Text → BERN2 NER/NEN → External Standard IDs（候选）
     → Entity Resolution Layer
     → Enterprise Ontology ID
        + external_ids[] + confidence + method + evidence
```

解析链（有命中即停）：

```text
enterprise_id → dictionary → xref → zingg_table → bern2_candidate → unmapped
```

- **BERN2**：识别 + 候选归一（含企业自定义词典：内部代号 → 标准 ID；专有名词金标 100%）
- **Resolver**：词典 / SSSOM / Zingg 预计算表；大表联动可后续评估 Splink
- 种子词典：`ontology/dictionary/enterprise_dictionary.yaml`
- 企业实体：`ontology/entities/enterprise_entities.yaml`
- Zingg matches：`ontology/mappings/zingg_matches.jsonl`
- evidence / assets 样例：`data/foundation/`（运行投影，非身份 SSOT）

## Knowledge = Claim + Provenance + Evidence

原则：**Knowledge ≠ Truth**。图侧存 claim + W3C PROV；Milvus 存可引用原文（quote / span / doc_id）。

GraphDB **单 Repository + Named Graphs**：

```text
graph:biomedical   ← BIOS_v3
graph:ontology     ← Enterprise Ontology TBox + 映射
graph:knowledge    ← 企业断言（管线、实验结果关系等）
graph:provenance   ← PROV / claim 元数据
graph:inference    ← 推导关系（可选物化）
```

## Semantic Ops

| Op | 后端 |
|---|---|
| `resolve_entity` | BERN2 + Resolver（词典 / Zingg） |
| `get_entity` | **GraphDB** ontology graph（不可用则报错，禁止 YAML） |
| `get_relationships` | **GraphDB** knowledge + provenance |
| `find_related_entities` | **GraphDB** |
| `search_evidence` | **Milvus** `foundation_evidence` |
| `search_assets` | **OpenMetadata** Glossary `HMDEnterpriseAssets`（幂等 upsert） |
| `get_entity_evidence` | Milvus |
| `get_entity_assets` | OpenMetadata |
| `get_entity_context` | GraphDB + Milvus + OM（**禁止 YAML fallback**） |

入库：策展 YAML 在 `ontology/{entities,dictionary,claims}/`；evidence/assets 样例在 `data/foundation/`。
经 `ontology:validate` → `hmd foundation sync`（幂等）写入 GraphDB + Milvus + OM。查询层不读 YAML。

## 入湖流水线（目标形态）

```text
PubMed / Patents / ELN / LIMS / Assay / Docs
        → Prefect（后续）
        → parse（含多模态）
        → BERN2
        → Entity Resolution → Enterprise IDs
        → Knowledge + PROV → GraphDB
        → Evidence → Milvus
        → Assets/Glossary → OpenMetadata
```

## 联调栈（Taskfile）

| Service | 端口 | 备注 |
|---|---|---|
| Milvus | 19530 | Evidence Index，**始终必选** |
| GraphDB | 7200 | 10 Free 默认无 license；SE/EE 见 `docker-compose.graphdb-license.yml` |
| OpenMetadata | 8585 | Glossary / Asset 锚 `HMD:ENT:*` |
| BERN2 | 8888 | 先 `task foundation:bern2:fetch`（~70GB，Google Drive），再 `task foundation:up:bern2`。**自动切换**：macOS Apple Silicon → 原生 MPS；Linux + NVIDIA → CUDA Docker；否则 CPU Docker。覆盖：`BERN2_RUNTIME=docker\|native`、`BERN2_ACCEL=cuda\|cpu\|mps` |

```bash
# 许可 ACK（BIOS 全量默认；CI 用 subset）
export HMD_BIOS_LICENSE_ACK=poc   # 或 evaluation | licensed
# CI / 无许可证：export HMD_BIOS_INIT=subset

export HMD_BIOS_LICENSE_ACK=poc
export HMD_BIOS_MAX_CONCEPTS=0   # 全量不截断（流式灌库，勿 list 进内存）
task foundation:up             # 单一 compose 项目 hmd-foundation + smoke + BIOS init + sync
# BERN2 全量资源（另开终端，可长时间跑）：
task foundation:bern2:fetch
task foundation:bern2:detect   # Darwin→native/MPS；Linux+NVIDIA→docker/cuda
task foundation:up:bern2
# 强制 Docker CPU：BERN2_RUNTIME=docker BERN2_ACCEL=cpu task foundation:up:bern2
export HMD_BERN2_URL=http://localhost:8888
# 或仅起 Evidence Index（同项目子集，勿再起旧 hmd-milvus）：
task milvus:up
task foundation:smoke
uv run hmd foundation bios-load --full
uv run hmd foundation sync     # YAML → GraphDB + Milvus + OpenMetadata（三后端必达）

uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation evolve-mine   # unmapped → KGCL 候选（不改本体）
uv run hmd foundation zingg-run     # 校验预计算 matches
uv run hmd serve --mcp              # 单一 REST + MCP（含 get_entity_context）
```

| GraphDB 环境 | 选型 |
|---|---|
| PoC / Dev / 单用户 Demo | Free（≤5 repo、**2 concurrent queries**） |
| 生产 / 多 Agent | Standard / Enterprise（架构上写死升级路径） |

## 金路径

```text
DrugCandidate → Target → Disease → PubMed/Patent Evidence → ELN/LIMS Asset
```

验收别名示例：`HMPL-504` → `HMD:ENT:DC:savolitinib` → MET / NSCLC → 带 quote 的证据 → `exp_2025_012` 资产。

## P2：Data Loop（脚手架边界）

```text
Ontology → Knowledge →（仓外 Agent）→ New Evidence
  → Extraction candidates → Curation → Ontology Evolution → World Model
```

| 做 | 不做 |
|---|---|
| unmapped / 低置信 → `evolve-mine` → `.kgcl` + candidates JSON | 自动改 GraphDB ontology |
| 候选含建议别名 / suggested exactMatch | 自动策展 / `evolve-apply` |

复用 `src/biomed_ontology/evolution/` 与 Foundation `foundation/evolve.py`。

## 合规

- BIOS_v3：**CC-BY-NC-ND 4.0** — 生产前法务闸门；见
  [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md)
- 企业词典与内部实体：**不得**外泄到公共 BERN2 云 API（本地部署）
- 全量下载需磁盘容纳 HF 包 + 展开 RDF；Free 灌库期间错峰查询

## 开源栈与自研边界

| 能力 | 选型 |
|---|---|
| 公共 Biomedical KG | BIOS_v3 |
| 企业 Ontology | LinkML + SHACL（`hmd_enterprise`，唯一 SSOT） |
| Ontology 审阅 | Protégé（只读生成 OWL，不回写 SSOT） |
| RDF 工程层 | rdflib + pyshacl（**不引入 Jena**） |
| Runtime | GraphDB |
| NLU | BERN2 + 自定义词典 |
| Entity Resolution | 词典 / SSSOM + Zingg |
| Evidence Index | Milvus（默认 multimodal-bio 五列） |
| Data Context | OpenMetadata |
| Provenance | W3C PROV |
| Semantic Access | 薄 MCP / REST（`hmd serve --mcp`，主契约 `get_entity_context`） |

**自研 IP**：R&D Domain Ontology、BIOS↔Enterprise 映射、BERN2→Resolver 胶水、Semantic API、金路径数据与评测。

## 源码地图

| 路径 | 职责 |
|---|---|
| `schema/hmd_enterprise.yaml` | Enterprise Ontology SSOT |
| `ontology/` | Ontology-as-Code 策展面（mappings / Protégé 入口 / Golden Path） |
| `src/biomed_ontology/foundation/` | ids / bern2 / resolve / world / api / sync / bios / evolve / mcp |
| `data/foundation/` | entities / dictionary / claims / evidence / assets / BIOS subset / Zingg |
| `docker/docker-compose.foundation.yml` | 联调栈 |
| `Taskfile.yml` | `foundation:*` / `milvus:*` / `gen` / `ontology:validate` |

## 与既有 PoC 的演进关系

| 已有 | 演进 |
|---|---|
| LinkML schema | 扩展为 Enterprise Ontology SSOT（`hmd_enterprise`） |
| Normalizer | 降为 Resolver 一环 / 企业词典源 |
| GraphStore（曾用 oxigraph） | 统一 GraphDB；单测 respx mock + 少量集成测 |
| Citationware / PROV | 升级为图侧一等 Provenance |
| Milvus 多模态 | Evidence Index；`entity_ids` → Enterprise ID |
| MCP 工具 | 收敛为单一 `hmd serve` 上的 Semantic Ops + KB 工具 |
| `evolution/` | Data Loop 脚手架（`evolve-mine`） |
