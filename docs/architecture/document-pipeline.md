# Document → Evidence → Knowledge → World Model

双线并行入湖：**不是**全量文档转 Ontology。文档入湖负责 Evidence Index 与抽取候选；仅经策展 `validated` 的 claim 才物化企业知识图边。

源码：`src/biomed_ontology/lake/`（`steps.py`、`flows.py`）、`src/biomed_ontology/corpus/tree.py`。

抽取与接地细则见 [事实抽取（TriModal）](../ontology/extract.md)；概念身份见 [目录 SSOT](../ontology/seed.md) / [Normalizer](../ontology/normalize.md)。

---

## 1. 为什么存在

研发文档（PDF、专利、ELN 导出、供应商报告）是证据与候选知识的来源，但不是世界模型本身。若把「每篇文档」直接变成 ontology class，会得到不可治理的爆炸图。

本流水线把入湖拆成两条**并行腿**：

- **Evidence 腿**：原文切片 → Milvus + Iceberg，回答「证据在哪、原文怎么说」
- **Knowledge 腿**：BERN2 + ER → TriModal 抽 (S,P,O) → extracted claims → 策展后 validated → GraphDB knowledge

OpenMetadata 经 Trino 治理湖表元数据；文档 Asset 单独登记。关系真相仍在 GraphDB。

### 1.1 文档挂到已有 Ontology，不新增概念

| 会新增（自动） | 不会由文档自动新增 |
|---|---|
| Evidence（Tree Chunk） | Ontology 概念节点（药/靶点/适应症…） |
| Claim（`claim_status=extracted`） | `graph/knowledge` 正式边（需 validated 策展） |
| PROV 溯源（`provenance_extracted`） | catalog / entities 里的身份（需人工写回 Git） |

文档路径：**切证据 → 挂已有 `HMD:ENT:*` → 抽候选事实**。  
概念路径：**`ontology/catalog/` → build → Normalizer 词典**（金路径实体另见 `ontology/entities/`）。

挂不上 catalog 的词 → unmapped / `_ground` 失败 → 事实丢弃或进演进信号；**新概念靠 PR 改 catalog，不是读 PDF 自动 mint**。

---

## 2. 设计取舍

| 决策 | 理由 | 拒绝的方案 |
|---|---|---|
| 双线并行 | Evidence 与 Claim 生命周期不同 | 文档全文灌 ontology |
| 文档不 mint 概念 | 身份可策展、可发版 | 每篇文档发明 ontology class |
| BERN2 硬依赖（ingest） | 抽取候选需 NLU 锚点 | ingest 无 NER 静默跳过 |
| `extracted` ≠ `validated` | 抽取垃圾不进 knowledge 边 | 自动物化所有三元组 |
| 同 `doc_id` 幂等先删后写 | 重跑不翻倍 | append-only 湖表 |
| Tree Chunk | 保留 section_path 供 Citationware | 固定长度盲切 |
| Prefect 可选 | 复杂编排 vs 纯函数 CLI | 只有 GUI 编排 |

---

## 3. 设计与实现

### 3.1 总览

```text
Document (MinIO)  PDF / DOCX / PPTX / XLSX …
   │
   ▼
Document Router → PyMuPDF4LLM | Docling | MinerU
   │
   ▼
Canonical Document → Semantic Tree → Tree Chunk Engine
   │
   ├────────────────────────────┐
   ▼                            ▼
Evidence Index               Claim Extraction
Milvus + Iceberg             BERN2 → ER → TriModal → Claims
Evidence Object              claim_status=extracted
   │                            │
   │                     Iceberg + GraphDB provenance_extracted
   │                            │（策展后写回 ontology/claims）
   │                     validated → GraphDB knowledge
   │
OpenMetadata ← Trino（官方 connector）← Iceberg REST Catalog
```

### 3.2 四层存储

| Layer | 存哪 | 回答什么 | 主键 |
|---|---|---|---|
| Document | MinIO `hmd-documents` | 原文在哪 | `doc_id` |
| Evidence | Milvus + Iceberg `evidence_chunks` | 证据在哪 | `evidence_id` / `chunk_id` |
| Knowledge | Iceberg `knowledge_claims` + GraphDB provenance | 抽了什么候选 | `claim_id` |
| World Model | GraphDB `graph/knowledge`（仅 validated） | 企业认可的关系 | Enterprise ID 边 |

### 3.3 Tree Chunk = Evidence Object

节点层级：`document → section/subsection → paragraph → sentence`，另挂 `table` / `figure` / `caption`。

字段：`chunk_id`, `parent_id`, `document_id`, `section_path`, `node_kind`, `content`, `entity_ids[]`。

`entity_ids` 在 ingest 后经 ER 填入 `HMD:ENT:*`（非 BIOS 作主键）。用途：检索、引用、审校时回到「第几节哪句话」——回答「证据在哪」，不回答「企业正式认定什么关系」。

模块：`corpus/tree.py`；解析路由见 `parse/router.md`；字段细则见 [切片](../parse/chunks.md)。

文献 `build_literature_base` / `hmd index` 与 lake ingest **共用** `build_document_tree` + `tree_to_chunks`；`hmd index` dual-write Milvus `hmd_chunks` 与 Iceberg `evidence_chunks`（同 `chunk_id`、同 `release_id`）。Citationware 经 `ChunkStore` 从 Iceberg 懒加载还原。

### 3.4 Knowledge 腿：TriModal → extracted Claim

```text
annotate_bern2 → chunk.entity_ids（HMD:ENT:*）
  → TriModalPipeline（LLM / 规则 / 表）
  → _ground(Normalizer←catalog) 强制 S/O 为企业 CURIE
  → facts_to_claims（claim_status=extracted）
  → Iceberg knowledge_claims + GraphDB provenance_extracted
```

- **Fact**：带 evidence quote 的结构化三元组候选（`ExtractedFact` → `KnowledgeClaim`）。
- **BERN2**：上游 NER，不是关系抽取引擎；TriModal 核心也可在无 BERN2 时靠 Normalizer detect（覆盖较差）。
- **BIOS_v3**：不参与接地；正式知识边只认策展 `validated` claims。

算法、产出表、审校写回 catalog / claims 的完整说明见 [事实抽取](../ontology/extract.md)。

### 3.5 编排入口

| 入口 | 模块 | 用途 |
|---|---|---|
| `hmd index` | `cli.py` | Tree Chunk → Milvus + Iceberg |
| `hmd lake ingest-doc` | `lake/steps.py` | 纯函数单文档 |
| `hmd lake ingest-flow` | `lake/flows.py` | Prefect 编排 |
| `hmd lake ingest-batch` | `lake/flows.py` | 批量 |

硬依赖：`HMD_BERN2_URL`；不可达则失败。ingest **禁止**自动把 claim 标为 `validated`。

### 3.6 GraphDB 写入与 sync 边界

| 图 URI | 写入方 | `foundation sync` 行为 |
|---|---|---|
| `graph/provenance_extracted` | lake ingest | **不清空** |
| `graph/provenance` | `foundation sync`（seed claims） | replace |
| `graph/knowledge` | 仅 validated claims | replace（sync） |

`foundation/sync.py` 的 `append_extracted_claims` 供湖侧增量写入 extracted 图。

### 3.7 幂等（同 `doc_id` 重跑）

| Sink | 策略 |
|---|---|
| MinIO | 同 object key overwrite |
| Iceberg `documents` / `evidence_chunks` / `knowledge_claims` | 按 `doc_id` / `document_id` **先删后写** |
| Milvus `foundation_evidence` | 按 `doc_id` 删孤儿后 upsert（`evidence_id` 主键） |
| GraphDB extracted | 按 `hmd:sourceId` 先删后写 |
| OpenMetadata document asset | glossary term upsert by FQN |

---

## 4. 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| BERN2 双写硬依赖 | 无 NLU 的 claim 不可信 |
| extracted ≠ validated | 垃圾边污染 World Model |
| extracted 图独立 | sync CLEAR → 湖侧 claim 全丢 |
| 双线并行 | 全文档转 ontology → 不可策展 |
| Evidence `entity_ids` 用 ENT | BIOS 作主键 → 与 Foundation 分裂 |
| 同 doc_id 幂等 | 重跑翻倍 → 评测与引用错乱 |

| 失败模式 | 处理 |
|---|---|
| BERN2 超时 | ingest 失败，不部分提交 knowledge |
| Iceberg catalog 不可达 | `hmd lake init` / `ensure` 先修 |
| 未策展 extracted | GraphDB knowledge 无新边（预期） |

BIOS_v3 正常挂载 `graph/biomedical`（`hmd foundation bios-load`）。UMLS 等扩展见 `foundation/biomedical_sources.py`。

---

## 5. 如何验证

```bash
task foundation:up
uv run hmd lake ensure && uv run hmd lake init
uv run hmd lake trino-smoke
export HMD_BERN2_URL=http://localhost:8888
uv run hmd lake ingest-doc --help
uv run pytest tests/ -k lake -q 2>/dev/null || true
```

相关：[事实抽取](../ontology/extract.md)、[OpenMetadata × Trino](openmetadata.md)、[Foundation](foundation.md)、[解析路由](../parse/router.md)、[不变量](../invariants.md)。
