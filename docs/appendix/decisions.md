# 设计决策 D1–D12

散落在 `schema/*.yaml` 与代码注释中的决策索引。无独立 ADR 目录 —— 以 schema 为 SSOT 时，决策写在约束旁边比写在另册更不容易腐烂。

| ID | 要旨 | 主要落点 | 手册深入 |
|---|---|---|---|
| D1 | 内部 CURIE 是主键，不用外部 ID | `ontology/ids.py`、`hmd_concept.yaml` | [种子](../ontology/seed.md) |
| D2 | 别名必须带 scope，扩展行为由 scope 驱动 | `alias/`、`normalize/matchers.py` | [归一化](../ontology/normalize.md) |
| D3 | 消歧不确定时返回 top-k，不猜 | `NormalizeResponse` 备选 | [归一化](../ontology/normalize.md) |
| D5 | 未经人工审校不得进 tool 返回体（生成内容 PENDING） | 质量 / 映射状态枚举 | [演进](../evolution/loop.md) |
| D6 | provenance / trace_id 是返回体一等公民 | `Provenance`、`Feedback`、`TraceContext` | [Citationware](../tools/citationware.md)、[四支柱](../observability/pillars.md) |
| D7 | 映射依据词表统一；归一化阶段可观测 | `MappingJustification`、级联阶段 | [归一化](../ontology/normalize.md) |
| D9 | 外部 ID 一律 xref / SSSOM 挂靠 | Concept Mapping | [种子](../ontology/seed.md) |
| D10 | 许可分层贯穿全链路 | `licensing.py`、named graph、LicenseScope | [Tier](../licensing/tiers.md)、[RDF](../ontology/rdf.md) |
| D11 | （与 D10 配套）许可感知 RDF 查询 | `ontology/rdf.py` | [RDF](../ontology/rdf.md) |
| D12 | 仅归一化不够；要有事实层与质量层才叫底座 | `hmd_fact.yaml` | [分层](../architecture/layers.md) |
| D13 | Evidence ∥ Claim 双线并行，不全量转 Ontology | `lake/ingest.py` | [Document Pipeline](../architecture/document-pipeline.md) |
| D14 | ingest 默认 `claim_status=extracted`；validated 才进 knowledge | `hmd_enterprise.yaml` | [Document Pipeline](../architecture/document-pipeline.md) |
| D15 | 双写硬依赖 BERN2；Tree Chunk 为正式 Evidence Object | `corpus/tree.py`、`lake/steps.py` | [chunks](../parse/chunks.md) |
| D16 | 复杂入湖用 Prefect；业务逻辑只在 steps | `lake/flows.py` | [Document Pipeline](../architecture/document-pipeline.md) |
| D17 | BIOS_v3 常挂 `graph:biomedical`；UMLS 等可扩展 | `bios.py`、`biomedical_sources.py` | [Foundation](../architecture/foundation.md) |
| D18 | OM 经 Trino 官方 connector 治理 Iceberg；不止 Glossary | `lake/om_governance.py` | [OpenMetadata](../architecture/openmetadata.md) |
| D19 | 多格式 Document Router；Docling Main、PyMuPDF4LLM Fast、MinerU Hard | `parse/router.py` | [router](../parse/router.md) |
| D20 | 废弃 `pymupdf` LayoutBackend，统一为 `pymupdf4llm` | `parse/layout/` | [layout](../parse/layout.md) |

实现细节以源码与 schema 描述为准；本表供跳转。新增决策时：写进相关 schema 字段描述 + 本表一行 + 必要时加不变量条目。
