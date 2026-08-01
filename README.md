# biomed-ontology

面向阿斯利华创新药研发场景的**生物医药语义层数据基座** PoC。

为 AI agent 提供可溯源的检索能力：Ontology 语义层（谁是谁）+ 结构化事实层（发生了什么）+
文档层（在哪说的）+ 质量层（多可信），配套四支柱可观测与本体演进闭环。

**本仓库不包含 AI agent 本身** —— 只构建其消费的数据底座与工具接口。

## 快速开始

```bash
uv sync --all-extras
uv run hmd --help
```

## 架构

```
L0 Source        构建期联网拉快照 → 版本化存储（version / license / retrieved_on）
L1 术语层        Concept / Synonym / Xref(SSSOM) / Hierarchy → RDF named graph per source
L2 语义层        LinkML schema（Biolink 子集）→ OWL + SHACL + JSON Schema
L3 归一化        文本 → 唯一 CURIE（词典 → 规则 → 向量 → LLM 消歧）
L4 语料治理      文档标引分类 + 三模态抽取（文本/表格/图像）→ 结构化事实 + provenance
L5 检索/查询     OpenSearch(BM25) ⊕ Milvus(dense) → RRF → rerank；SPARQL 图查询
L6 Agent 接口    MCP Server + REST，返回体内建 provenance + trace_id + license_tier
L7 可观测        Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)
L8 演进闭环      Signal → Candidate → Curation(KGCL) → Release → Impact → 回归守门
```

## 目录

| 路径 | 职责 |
|---|---|
| `schema/` | LinkML 模型定义，单一事实来源。生成 OWL / SHACL / JSON Schema / Python |
| `src/biomed_ontology/registry/` | 数据源注册表 + 许可分层（license tier） |
| `src/biomed_ontology/ontology/` | 等价团构建、ID 分配、发版 |
| `src/biomed_ontology/ingest/` | 各数据源 loader（open / licensed 双轨） |
| `data/seed/` | PoC 种子切片：阿斯利华自研管线 + 竞品 |
| `tests/` | 契约与不变量测试 |

## 核心设计约束

- **内部 CURIE 是唯一主键**，外部 ID 一律作为 xref 挂靠（供应商中立）
- **别名必须带 scope**，检索扩展行为由 scope 驱动
- **许可分层贯穿全链路**，tier≥2 内容不得进入导出物与训练语料
- **构建期可联网，运行期完全内网离线**
