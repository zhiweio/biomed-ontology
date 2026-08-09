# OpenMetadata × Trino × Iceberg

OpenMetadata 是 **Enterprise Data Context / Governance**，不是第二套 Knowledge Graph。

## 联调栈

| 组件 | 端口 | 角色 |
|---|---|---|
| MinIO | 9000 | `hmd-documents` / `hmd-lake` |
| Iceberg REST | 8181 | PyIceberg 与 Trino 共享 catalog |
| Trino | 8080 | Iceberg connector → 湖表 SQL |
| OpenMetadata | 8585 | Catalog / Lineage / Glossary / Tags |

```bash
task foundation:up   # 含 iceberg-rest + trino
uv sync --extra lake
uv run hmd lake ensure && uv run hmd lake init
uv run hmd lake trino-smoke
uv run hmd lake om-ingest
```

## 能力用法

| OM 能力 | 用法 |
|---|---|
| DatabaseService (Trino) | `HMDTrinoLake`；官方 metadata/lineage ingestion |
| Table / Column | `iceberg.hmd.documents|evidence_chunks|knowledge_claims` |
| Domain / Owner / Tags | R&D Intelligence、Literature、ExtractedClaim… |
| Glossary | 企业资产 / 业务术语 ↔ `HMD:ENT:*` |
| Lineage | Trino ingestion + ingest 补充 Doc→Milvus/GraphDB 边 |

## 硬约束

- 关系真相在 GraphDB；证据在 Milvus
- **禁止** BIOS / UMLS / BERN2 NER 实体全量进 Glossary
- 文档 Asset = 数据资产身份，不是知识断言
