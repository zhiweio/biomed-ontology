# IngestQA：入湖质检

源码：`src/biomed_ontology/lake/ingest_qa.py`。
接线：`lake/ingest.py` 在 `parse_and_tree` 之后调用 `run_ingest_qa`，报告写入 `IngestContext.qa`。

IngestQA **只管文档能否入库**，不管 KB 发版。发版守门是 `quality.QualityGate`。

---

## 1. 为什么存在

解析降级、空语义树、未登记来源、缺 `doc_id` 的文档一旦静默入库，Evidence Index 与 Citationware 会用垃圾切片冒充可引用证据。入湖必须有一道**大声失败**的闸门，且不能和「claim 能不能进 knowledge 图」混成同一个 QualityGate。

---

## 2. 设计取舍

| 决策 | 理由 | 放弃 |
|---|---|---|
| 与 QualityGate 分开 | 入库 vs 发版生命周期不同 | 一个闸门两用 |
| 默认 `strict=True` | 失败抛 `IngestQAError` | 警告后继续写湖 |
| 降级按已知能力集比例 | 缺 bbox/ocr/formula/table 超过阈值才阻断 | 任意 warning 都阻断 |
| `doc_id` 作幂等键 | 重跑先删后写 | 文件名当主键 |

---

## 3. 设计与实现

### 3.1 检查项

| 检查 | 阻断条件 |
|---|---|
| `doc_id` | 空，或含空白 |
| 语义树 | 无非空 chunk |
| 版面降级 | `parse_degraded ∩ {bbox,ocr,formula,table_structure}` 比例 > 0.4 |
| `source_id` | 空，或未在 `registry` 登记（registry 不可读 → warning，不阻断） |
| `license_tier` | 文档对象存在但未登记 |

`parse_and_tree` 把 Router 的 `degraded` 写入 `IngestContext.parse_degraded`，供本闸门消费。

### 3.2 报告

```text
IngestQAReport
├── passed
├── blocking[]
├── warnings[]
└── checks{doc_id, chunk_count, nonempty_chunks, degraded, degraded_ratio, source_id, license_tier}
```

`strict=True` 且未通过 → 抛 `IngestQAError`（消息含全部 blocking）。

### 3.3 在流水线中的位置

```text
Document Router → Semantic Tree → Tree Chunk
    → IngestQA          # 本页
    ├─ Evidence Index（Milvus + Iceberg）
    └─ Claim Extraction（BERN2 → IdentityService → TriModal）
```

详见 [Document Lake](../architecture/document-pipeline.md)。

---

## 4. 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 空树不得入库 | Citationware 还原空章节 |
| 降级超阈不得入库 | 视觉列 / 表格抽取建立在假能力上 |
| 无 `doc_id` 不得入库 | 幂等先删后写无法定位 |
| 失败要大声 | 静默入库后评测与引用错乱 |

---

## 5. 如何验证

```bash
uv run pytest tests/test_ingest_qa.py -q
uv run hmd lake ingest-doc --help
```

相关：[Document Lake](../architecture/document-pipeline.md)、[Router](router.md)、[QualityGate / 抽取](../ontology/extract.md)。
