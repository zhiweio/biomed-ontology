# OpenMetadata × Trino × Iceberg

OpenMetadata 在本架构中是 **Enterprise Data Context / Governance** 层，不是第二套 Knowledge Graph。关系真相在 GraphDB；证据在 Milvus；湖表元数据与血缘经 Trino 官方 connector 进入 OpenMetadata。

源码：`src/biomed_ontology/foundation/catalog.py`（`OpenMetadataClient`）、`src/biomed_ontology/lake/`。

---

## 1. 为什么存在

企业研发数据散落在 MinIO 原文、Iceberg 湖表、Milvus 向量集合、GraphDB 三元组与 ELN/LIMS 系统中。Agent 与数据工程师需要回答：

- **这张表是什么、谁拥有、从哪条流水线来？**（血缘）
- **这个 ELN 实验记录对应哪个候选药实体？**（`HMD:ENT:*` 锚点）
- **文档 Asset 与 Evidence 条目如何关联？**

OpenMetadata 提供 Catalog、Lineage、Glossary、Tags；**不**承担 biomedical 关系推理。

---

## 2. 设计取舍

| 决策 | 理由 |
|---|---|
| Iceberg 经 Trino 注册 OM | 官方 Database connector，血缘可自动采集 |
| Glossary 只锚企业资产/术语 | BIOS/UMLS 全量进 Glossary = 第二图谱 |
| 文档 Asset ≠ 知识断言 | Asset 是数据身份，不是 claim |
| `upsert_assets` 幂等 | `hmd foundation sync` 可重复执行 |
| OM 与 GraphDB 分工 | 关系查询走 Semantic Ops，不走 OM SQL |

---

## 3. 设计与实现

### 3.1 联调栈

| 组件 | 端口 | 角色 |
|---|---|---|
| MinIO | 9000 | `hmd-documents` / `hmd-lake` 对象存储 |
| Iceberg REST | 8181 | PyIceberg 与 Trino 共享 catalog |
| Trino | 8080 | Iceberg connector → 湖表 SQL |
| OpenMetadata | 8585 | Catalog / Lineage / Glossary / Tags |

```bash
task foundation:up   # 含 iceberg-rest + trino + openmetadata
uv run hmd lake ensure && uv run hmd lake init
uv run hmd lake trino-smoke
uv run hmd lake om-ingest
```

Compose：`docker/docker-compose.foundation.yml`。

### 3.2 能力用法

| OM 能力 | 用法 | 典型 FQN / 名称 |
|---|---|---|
| DatabaseService (Trino) | `HMDTrinoLake`；官方 metadata/lineage ingestion | — |
| Table / Column | 湖表结构 | `iceberg.hmd.documents`、`evidence_chunks`、`knowledge_claims` |
| Domain / Owner / Tags | 治理标签 | R&D Intelligence、Literature、ExtractedClaim… |
| Glossary | 企业资产 ↔ `HMD:ENT:*` | `HMDEnterpriseAssets` |
| Lineage | Trino ingestion + ingest 补充边 | Doc → Milvus / GraphDB |

### 3.3 Foundation sync 与 Semantic Ops

```text
hmd foundation sync
    → foundation.sync.sync_world_model()
        → OpenMetadataClient.upsert_assets(wm.assets)
```

| Semantic Op | 后端 |
|---|---|
| `search_assets` | OM Glossary 搜索 |
| `get_entity_assets` | 按 `HMD:ENT:*` 查关联 Asset |
| `get_entity_context` | GraphDB + Milvus + **OM** 聚合 |

`FoundationApi` 在 OM 不可达时抛 `BackendUnavailableError`，**禁止**读本地 YAML 冒充资产列表。

### 3.4 与 Document Pipeline 的衔接

```text
lake ingest-doc
    → Iceberg 表写入（documents / evidence_chunks / knowledge_claims）
    → Milvus foundation_evidence upsert
    → OM document asset upsert（按 FQN）
    → Trino 可见 → OM ingestion 采集血缘
```

ingest 补充的 lineage 边描述「文档 → 证据索引 / 抽取图」，不替代 GraphDB 关系边。`world_model_sync` / `data_loop_apply` 经 `runtime_lineage_meta` 写入 `prefect_run_id`、deployment、`ontology_release_id`。ELN/LIMS FQN 已在 `data/foundation/assets.yaml` 登记，不全量同步实验仓。

### 3.5 配置

Settings（`.env` / `config.py`）：

- `openmetadata_url`（默认 `http://localhost:8585`）
- `openmetadata_email` / `openmetadata_password`（sync 必填）

Iceberg 观测表（`obs_tool_io` / `obs_decision` / `obs_span` / `er_observations`）经 Trino 暴露后，同样走 OM Trino connector 摄入；观测总线本身配置见 [pillars](../observability/pillars.md)（`HMD_KAFKA_*` / `HMD_OBS_*`）。

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| OM ≠ 第二图谱 | 禁止 BIOS / UMLS / BERN2 NER 实体全量进 Glossary |
| 关系真相在 GraphDB | OM 不做 `has_target` 查询 |
| 证据在 Milvus | OM 不存 quote / span 正文 |
| 文档 Asset = 数据资产身份 | 不是 KnowledgeClaim |
| Glossary 锚 Enterprise ID | `entity_ids` / term 关联 `HMD:ENT:*` |
| sync 幂等 | 重复 upsert 不产生重复 term |

| 失败模式 | 处理 |
|---|---|
| OM ping 失败 | `foundation sync` 在 `require_om=True` 时硬失败 |
| Trino 连不上 Iceberg | `lake trino-smoke` 排查 REST catalog |
| 未跑 om-ingest | 湖表无元数据，血缘为空 |

---

## 5. 如何验证

```bash
task foundation:up
uv run hmd lake trino-smoke
uv run hmd lake om-ingest
uv run hmd foundation sync
uv run hmd foundation golden --candidate HMPL-504 --json   # backends 应含 openmetadata
```

相关：[Document Pipeline](document-pipeline.md)、[Foundation](foundation.md)、[Golden Path](../ontology/golden-path.md)、[不变量](../invariants.md)。
