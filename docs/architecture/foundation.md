# Enterprise Biomedical World Model（Foundation）

面向创新药研发的 **Enterprise Biomedical World Model / AI Data Foundation**：以 **Enterprise Ontology**（`HMD:ENT:*`）为锚，把公共生物医学概念、企业关系、可引用证据与 ELN/LIMS 资产连成可查询、可追溯的语义世界，经 `hmd serve`（MCP/REST）供仓外 Agent 使用。

> **BIOS provides the biomedical world. Enterprise Ontology provides the company's world.**

`hmd serve` 同时暴露 KB 侧 Ontology Semantic Layer（术语 / 层级 / 事实 / 检索 / Citationware）与 Foundation Semantic Ops（实体 / 关系 / 证据 / 资产 / Context Pack）。身份经 `IdentityService` 统一装配。别名与检索只是语义层中的两项，不是产品定义。

---

## 1. 为什么存在

公共本体（MONDO、ChEBI、DrugBank…）回答「行业里公认谁是谁」。企业研发还需要：

- **内部代号与管线实体**（HMPL-504、项目代号）
- **企业认可的关系断言**（候选药→靶点→适应症），带来源与置信度
- **可引用证据与资产锚点**（PubMed span、专利段落、ELN 实验记录）
- **跨系统统一主键**，供 GraphDB、Milvus、OpenMetadata、仓外 Agent 共用

Foundation 把上述能力收成**三后端投影**（GraphDB + Milvus + OpenMetadata），查询层只读运行时存储，不读策展 YAML。

---

## 2. 设计取舍

### 2.1 非目标

- 不做 LangGraph / vLLM / 多步 Agent 编排 / 报告自动生成
- 不做自研 RDF 引擎、Ontology Editor、ER 引擎、向量库、Catalog、Agent Runtime
- 仓外调用方**不得**把裸 `graph_sparql` / 原始向量 API 当主契约

### 2.2 核心角色判断

| 角色 | 是什么 | 不是什么 |
|---|---|---|
| **Enterprise Ontology** | 世界模型核心（DrugCandidate、Program、Assay…） | BIOS 子集 |
| **BIOS_v3** | 公共生物医学知识源（External Concepts） | 企业主键 owner |
| **BERN2** | Recognition + Candidate Normalization（NLU） | 通用 Entity Resolution |
| **Entity Resolution** | 企业身份解析 → `HMD:ENT:*` | 「BERN2 直出 BIOS URI」 |
| **GraphDB** | World Model 运行时（语义 / 关系 / 溯源） | 仅 PoC 玩具 |
| **Milvus** | **Evidence Index**（证据在哪） | 普通「向量库」话术 |
| **OpenMetadata** | **Enterprise Data Context**（资产在哪） | 仅 Glossary 打 BIOS 标签 |

### 2.3 三层 ID

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

---

## 3. 设计与实现

### 3.1 运行时栈

```text
External Agents
      │ MCP / REST
      ▼
Semantic Access (hmd serve)
  KB tools + Foundation ops
      ├─► GraphDB        关系 / 本体 / PROV
      ├─► Milvus         Evidence Index (foundation_evidence)
      └─► OpenMetadata   Data Context (HMDEnterpriseAssets)
            ▲
   Enterprise Ontology + LinkML / SHACL / PROV
            ▲
   BIOS (xref) · BERN2 · Dict · Zingg → HMD:ENT:*
```

| Layer | 核心问题 | 固定图 / 集合 |
|---|---|---|
| GraphDB | What is related to what? | `graph/ontology`、`graph/knowledge`、`graph/provenance`、`graph/biomedical` |
| Milvus | Where is the evidence? | `hmd_chunks` + `foundation_evidence`（`chunk_id` join） |
| OpenMetadata | Where is the enterprise data? | Glossary `HMDEnterpriseAssets` |

### 3.2 Entity Resolution

对外句柄是 `IdentityService.resolve_text`（`identity.py`）。有 `EntityResolver` 时走企业级联；否则回落目录 `normalize`。详见 [IdentityService](../ontology/identity.md)。

```text
Text → IdentityService.resolve_text
     → BERN2 NER/NEN → External Standard IDs（候选）
     → EntityResolver.resolve_mention / resolve_text
     → Enterprise Ontology ID + external_ids[] + confidence + method
```

解析链（`foundation/resolve.py`，有命中即停）：

```text
enterprise_id → external xref → dictionary → zingg_table → bern2_dictionary → unmapped
```

| 步骤 | `resolution_method` | 数据源 |
|---|---|---|
| 1 | `enterprise_id` | 输入已是 `HMD:ENT:*` |
| 2 | `xref` | `ResolutionIndex.by_external` |
| 3 | `dictionary` | `ontology/dictionary/` + 实体 aliases |
| 4 | `zingg` | `ontology/mappings/zingg_matches.jsonl` |
| 5 | `dictionary`（BERN2 词典注入） | `Bern2Client.dictionary` |
| 6 | `bern2_candidate` / `unmapped` | BERN2 仅出外部 ID，未映射企业实体 |

- 种子词典：`ontology/dictionary/enterprise_dictionary.yaml`
- 企业实体：`ontology/entities/enterprise_entities.yaml`
- 运行投影样例：`data/foundation/`（**非**身份 SSOT）

### 3.3 Knowledge = Claim + Provenance + Evidence

原则：**Knowledge ≠ Truth**。图侧存 claim + W3C PROV；Milvus 存可引用原文（quote / span / doc_id）。

GraphDB **单 Repository + Named Graphs**：

```text
graph/biomedical     ← BIOS_v3
graph/ontology       ← Enterprise Ontology TBox + 实体
graph/knowledge      ← validated 企业断言（关系边）
graph/provenance     ← seed 策展 claim 元数据
graph/provenance_extracted  ← 湖侧抽取 claim（sync 不清空）
graph/inference      ← 推导关系（可选物化）
```

`foundation/sync.py` 的 `sync_world_model`：

- `clear_graph(GRAPH_ONTOLOGY)` → 重载实体 TTL
- `clear_graph(GRAPH_KNOWLEDGE)` + `GRAPH_PROVENANCE` → 重载 validated claims
- **保留** `GRAPH_PROVENANCE_EXTRACTED`（湖侧 ingest 写入）

仅 `claim_status=validated` 且含 `object_id` 的 claim 物化 knowledge 边。

### 3.4 Semantic Ops

| Op | 后端 | 模块 |
|---|---|---|
| `resolve_entity` | BERN2 + EntityResolver | `foundation/api.py` |
| `get_entity` | GraphDB `graph/ontology` | `foundation/store.py` |
| `get_relationships` | GraphDB knowledge + provenance | `foundation/store.py` |
| `find_related_entities` | GraphDB | `foundation/store.py` |
| `search_evidence` | Milvus `hmd_chunks` 再按 `chunk_id` join `foundation_evidence`（占位向量 `embedded=false`） | `foundation/api.py` + `lake/evidence_join.py` |
| `search_assets` | OpenMetadata Glossary | `foundation/catalog.py` |
| `get_entity_evidence` | Milvus | `foundation/api.py` |
| `get_entity_assets` | OpenMetadata | `foundation/catalog.py` |
| `get_entity_context` | GraphDB + Milvus + OM → Context Pack | `foundation/api.py` + `context_pack.py` |

`get_entity_context` 是仓外推理的主契约：返回 `pack_version` / `identity` / `evidence_tree` / `license` / `missing[]`。详见 [Data-for-Agent](data-for-agent.md)。

查询层**禁止 YAML fallback**；`BackendUnavailableError` 在三后端不可达时硬失败。

### 3.5 入湖流水线（双线并行）

详见 [Document Pipeline](document-pipeline.md) 与 [OpenMetadata × Trino](openmetadata.md)。

```text
PubMed / Patents / Vendor / ELN / LIMS / Docs
        → MinIO（原文）
        → Prefect Flow / hmd lake ingest-doc
        → Tree Chunk Engine
        ├─► Evidence Object → Milvus + Iceberg
        └─► BERN2（必接）→ Claims(extracted) → Iceberg + GraphDB provenance_extracted
                （仅 validated 策展后 → GraphDB knowledge）
        → Trino ← Iceberg REST
        → OpenMetadata（官方 Trino connector）
```

### 3.6 源码地图

| 路径 | 职责 |
|---|---|
| `schema/hmd_enterprise.yaml` | Enterprise Ontology SSOT |
| `ontology/` | Ontology-as-Code 策展面 |
| `src/biomed_ontology/identity.py` | IdentityService（目录 + ER） |
| `src/biomed_ontology/foundation/` | ids / bern2 / resolve / world / api / context_pack / context_eval / sync / bios / evolve / evolve_kgcl / claim_promote |
| `src/biomed_ontology/pipelines/` | Prefect 生产平面（入仓 / 身份 / 闭环 / 发布 / replay / ops / claims） |
| `data/foundation/` | 运行投影样例、BIOS subset、Zingg |
| `docker/docker-compose.foundation.yml` | 联调栈 |
| `Taskfile.yml` | `foundation:*` / `milvus:*` / `gen` / `ontology:validate` |

### 3.7 联调栈

| Service | 端口 | 备注 |
|---|---|---|
| Milvus | 19530 | Evidence Index，**始终必选** |
| GraphDB | 7200 | Free 默认无 license；SE/EE 见 compose overlay |
| OpenMetadata | 8585 | Glossary / Asset 锚 `HMD:ENT:*` |
| BERN2 | 8888 | `task foundation:bern2:fetch` + `foundation:up:bern2` |

```bash
export HMD_BIOS_LICENSE_ACK=poc
export HMD_BIOS_MAX_CONCEPTS=0
task foundation:up
uv run hmd foundation bios-load --full
uv run hmd foundation sync
uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation evolve-mine
uv run hmd foundation evolve-enrich --from data/releases/foundation_candidates/<stamp>.candidates.json
uv run hmd serve --mcp
```

### 3.8 Data Loop（propose → approve → apply）

| 做 | 不做 |
|---|---|
| unmapped / 低置信 → `evolve-mine` → candidates JSON | 自动改 GraphDB ontology |
| policy filter + enrich → `proposals.jsonl`；人工 approve 后 `evolve-apply --write`（L1 别名 / L2 xref） | 无人审校 apply；L3 自动 create node；硬编码单次噪声串 |
| `claim-review` → `claim-promote --write` 只写 `ontology/claims/` | Prefect `INSERT` knowledge 边 |
| 观测事件 → Redpanda → Iceberg `obs_tool_io` / `er_observations` | 自研 ObsShipper；热路径同步 Iceberg append |
| `zingg-run` 物化/导出模糊 matches；输入指纹未变跳过 `train-link` | 查询路径 Spark；BIOS 全量当 master |
| `source-load --source hgnc` 从 catalog/entities xref 装公开基因 | 改 `HMD:ENT:*`；无 ACK 装 UMLS |

详情见 [演进闭环](../evolution/loop.md)。复用 `foundation/evolve.py`、`evolve_propose.py`、`evolve_apply.py`、`zingg_io.py`、`lake/obs_events.py`。

### 3.9 观测入湖与 Zingg 配置

均经 `biomed_ontology.config.Settings`（环境变量前缀 `HMD_`，见仓库根 `.env.example`）。

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `HMD_OBS_EVENTS_ENABLED` | `true` | 总开关 |
| `HMD_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:19092` | 默认 Redpanda；设空=Jsonl WAL |
| `HMD_KAFKA_OBS_TOOL_IO_TOPIC` | `hmd.obs.tool_io` | 工具遥测 topic |
| `HMD_KAFKA_ER_OBSERVATIONS_TOPIC` | `hmd.er.observations` | ER 缺口 topic |
| `HMD_OBS_WAL_DIR` | `data/obs_wal` | WAL 目录 |
| `HMD_ZINGG_MIN_SCORE` | `0.8` | matches 生效 / export 阈值 |
| `HMD_ZINGG_WINDOW_DAYS` | `30` | 扫 `er_observations` 窗口 |
| `HMD_ZINGG_MIN_OCCURRENCES` | `1` | 物化最低出现次数 |
| `HMD_ZINGG_OBSERVATIONS` | `all` | 物化 observation 源 |
| `HMD_ZINGG_SKIP_DOCKER` | `false` | 跳过 `docker/zingg` |
| `HMD_EVOLVE_INCLUDE_LAKE` | `true` | evolve-mine 默认合并湖信号 |
| `HMD_ENV` | `dev` | `prod` 禁止 `identity_match_dev`；生产 + `zingg_skip_docker` 告警 |

```bash
task obs:up       # Redpanda :19092（Settings 默认已指向）
uv run hmd lake init
task zingg:run    # 或 uv run hmd foundation zingg-run --mode stub-link
```

---

## 4. 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| Milvus 必选 | 文献检索与 Evidence Index 回落内存 → 评测/服务撒谎 |
| Semantic Ops 隐藏后端 | Agent 拼 SPARQL → 许可旁路 |
| Knowledge ≠ Truth | 把 extracted 当 validated → 图里垃圾边 |
| extracted 图独立 | `foundation sync` CLEAR extracted → 湖侧 claim 丢失 |
| BIOS 许可闸门 | 未 ACK 灌全量 → 合规风险 |
| GraphDB Free ≠ 生产 | 2 concurrent queries 瓶颈被误当生产架构 |
| 企业词典不外泄 | 内部代号进公共 BERN2 云 API |

| 失败模式 | 处理 |
|---|---|
| GraphDB unreachable | `sync` / `get_entity` 抛 `BackendUnavailableError` |
| `foundation_evidence` 不存在 | 先 `hmd foundation sync` |
| BERN2 不可达 | `lake ingest-*` 硬失败；`resolve` 降级为词典/xref 路径 |
| BIOS 空图 | `golden-eval` 检查 `graph/biomedical` 非空 |

---

## 5. 如何验证

```bash
task foundation:smoke
uv run hmd foundation sync
uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation golden-eval --compact
uv run hmd foundation source-load --source hgnc
uv run python scripts/ontology_cheap_ci.py
uv run pytest tests/test_foundation_world_model.py tests/test_ops_p2.py tests/test_biomedical_sources.py -q
uv run pytest -m integration   # 需 task foundation:up
```

金路径验收别名：`HMPL-504` → `HMD:ENT:DC:savolitinib` → MET / NSCLC → 带 quote 的证据 → ELN 资产。详见 [Golden Path](../ontology/golden-path.md)。

合规：BIOS_v3 **CC-BY-NC-ND 4.0** — 见 [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md)、[组件闸门](../licensing/components.md)。

开源栈边界：LinkML + rdflib + pyshacl + GraphDB + BERN2 + Milvus + OpenMetadata。图引擎只认 GraphDB。自研 IP：R&D Domain Ontology、BIOS↔Enterprise 映射、IdentityService、Semantic API、金路径数据与评测。
