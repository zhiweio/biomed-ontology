# Ontology Engineering Toolchain

把 Protégé、LinkML、RDF 工程层、SHACL、GraphDB 的职责钉死，避免互相争夺真相源。

> **我们不构建 Agent，我们构建 Agent 所依赖的 Biomedical World Model。**

## 决策（A1）

- **LinkML 是唯一 SSOT**（[`schema/`](../../schema/)）。
- **Protégé 只审阅**生成的 OWL，禁止成为第二真相源。
- **不引入 Apache Jena**；RDF 工程层由 **rdflib + pyshacl**（及 GraphDB / oxigraph）承担。

## 工具链

```text
                    ┌─────────────────────┐
                    │     Protégé         │
                    │ Ontology Review     │
                    └──────────┬──────────┘
                               │ read-only OWL
                               ▼
                    ┌─────────────────────┐
                    │      LinkML         │
                    │ Domain Schema SSOT  │
                    └──────────┬──────────┘
                               │ task gen
                               ▼
                    ┌─────────────────────┐
                    │ rdflib + pyshacl    │
                    │ RDF / SHACL / ETL   │
                    └──────────┬──────────┘
                               │ validation
                               ▼
                    ┌─────────────────────┐
                    │       SHACL         │
                    │ Quality Gate        │
                    └──────────┬──────────┘
                               │ valid RDF
                               ▼
                    ┌─────────────────────┐
                    │      GraphDB        │
                    │ World Model Runtime │
                    └─────────────────────┘
```

| 组件 | 负责 | 不负责 |
|---|---|---|
| **LinkML** | Enterprise Domain Schema / Canonical Model | 运行时查询 |
| **Protégé** | 语义审阅（class / axiom） | 写回 SSOT |
| **rdflib / pyshacl** | RDF parse / transform / SHACL / sync | 世界模型存储 |
| **SHACL** | 「入图数据是否符合约束」 | 定义世界模型本身 |
| **GraphDB** | Runtime 事实来源 | 本体编辑 |

## Ontology as Code

Git 策展面：[`ontology/`](../../ontology/)（映射、样例、Protégé 入口）。

LinkML SSOT 仍在 `schema/`，生成物在 `schema/generated/`。

```bash
task gen                 # LinkML → pydantic / JSON Schema / SHACL / OWL
task ontology:validate   # 目录 + 映射对齐 + Golden Path
task ontology:sync-artifacts  # 可选：复制 OWL/SHACL 到 ontology/
```

PR 流程：

```text
Git → PR → ontology:validate →（人工 Protégé 审阅可选）→ merge
         → 本地 foundation:sync → GraphDB
```

## 三层 ID

```text
① Enterprise Entity   HMD:ENT:{DC|TGT|IND|…}:slug
② External Concept    BIOS:… / CHEBI:… / HGNC:…
③ Evidence            pubmed:… | patent:… | eln:… | lims:… | ev:…
```

```text
Enterprise Entity
        │
        ├── skos:exactMatch → External Concept
        │
        └── KnowledgeClaim.evidence_ids → Evidence
```

Graph / Milvus / API 主键仍是 **Enterprise ID**。详见 [Foundation](../architecture/foundation.md)。

## 与 Jena 的边界

业界常见的「Jena = RDF engineering」角色，在本仓库由：

- `src/biomed_ontology/ontology/rdf.py`（PoC GraphStore / oxigraph）
- `foundation/sync.py` + `foundation/graphdb.py`
- `quality/` + pyshacl

承担。**第一版不部署 Jena 服务。**
