# Document → Evidence → Knowledge → World Model

双线并行入湖（**不是**全量文档转 Ontology）：

```text
Document (MinIO)
   │
   ▼
Semantic Parsing → Tree Chunk Engine
   │
   ├────────────────────────────┐
   ▼                            ▼
Evidence Index               Claim Extraction
Milvus + Iceberg             BERN2（必接）→ ER → Claims
Evidence Object              claim_status=extracted
   │                            │
   │                     Iceberg + GraphDB provenance
   │                            │（策展后）
   │                     validated → GraphDB knowledge
   │
OpenMetadata ← Trino（官方 connector）← Iceberg REST Catalog
```

## 四层

| Layer | 存哪 | 回答什么 |
|---|---|---|
| Document | MinIO `hmd-documents` | 原文在哪 |
| Evidence | Milvus + Iceberg `evidence_chunks` | 证据在哪 |
| Knowledge | Iceberg `knowledge_claims` + GraphDB provenance | 抽了什么候选 |
| World Model | GraphDB knowledge（仅 `validated`） | 企业认可的关系 |

## Tree Chunk = Evidence Object

节点：`document → section/subsection → paragraph → sentence`，另挂 `table` / `figure` / `caption`。  
字段：`chunk_id`, `parent_id`, `document_id`, `section_path`, `node_kind`, `content`, `entity_ids[]`。

源码：`src/biomed_ontology/corpus/tree.py`。

## 编排

- 纯函数：`hmd lake ingest-doc`（`lake/steps.py`）
- Prefect：`hmd lake ingest-flow` / `ingest-batch`（`lake/flows.py`）
- 硬依赖：`HMD_BERN2_URL`；不可达则失败
- ingest **禁止**自动 `validated`

## 幂等（同 `doc_id` 重跑）

| Sink | 策略 |
|---|---|
| MinIO | 同 object key overwrite |
| Iceberg `documents` / `evidence_chunks` / `knowledge_claims` | 按 `doc_id` / `document_id` **先删后写** |
| Milvus `foundation_evidence` | 按 `doc_id` 删孤儿后 upsert（`evidence_id` 主键） |
| GraphDB | 湖侧写入 `graph/provenance_extracted`；按 `hmd:sourceId` 先删后写 |
| OpenMetadata document asset | glossary term upsert by FQN |

`hmd foundation sync` 只 replace seed 的 `graph/provenance`，**不清** `graph/provenance_extracted`。

## BIOS

BIOS_v3 **正常挂载** `graph:biomedical`（`hmd foundation bios-load`）。UMLS 等见 `foundation/biomedical_sources.py` 扩展点。

## OpenMetadata × Trino

Iceberg 经 **Trino + OM 官方 Database connector** 做表元数据与血缘；文档 Asset 单独登记。禁止把 NER/BIOS 实体灌进 Glossary。
