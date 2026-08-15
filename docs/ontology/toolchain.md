# Ontology Engineering Toolchain

把 Protégé、LinkML、RDF 工程层、SHACL、GraphDB 的职责钉死，避免互相争夺真相源。

> **我们不构建 Agent，我们构建 Agent 所依赖的 Biomedical World Model。**

---

## 1. 为什么存在

Enterprise Ontology 需要同时满足：

- **机器可读契约**（Pydantic / JSON Schema / MCP）
- **语义互操作**（OWL / SKOS / SSSOM）
- **入图质量闸门**（SHACL）
- **运行时查询**（GraphDB）

若没有明确 toolchain 边界，团队会在 Protégé、手写 TTL、业务 Python 之间反复争论「哪个是 SSOT」，导致 sync 与 Semantic Ops 读到不同版本的世界。

---

## 2. 设计取舍（A1）

| 决策 | 负责 | 不负责 |
|---|---|---|
| **LinkML 唯一 SSOT** | Domain Schema / Canonical Model | 运行时查询 |
| **Protégé 只审阅** | class / axiom 语义审查 | 写回 SSOT |
| **rdflib + pyshacl** | RDF parse / transform / SHACL / ETL | 世界模型存储 |
| **SHACL** | 「入图数据是否符合约束」 | 定义世界模型本身 |
| **GraphDB** | Runtime 事实来源（唯一图引擎） | 本体编辑 |

RDF 工程（parse / transform / SHACL / ETL）由 `ontology/rdf.py`、`foundation/sync.py`、`quality/` 承担。

---

## 3. 设计与实现

### 3.1 工具链总览

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
                    │   schema/*.yaml     │
                    └──────────┬──────────┘
                               │ task gen
                               ▼
                    ┌─────────────────────┐
                    │ rdflib + pyshacl    │
                    │ RDF / SHACL / ETL   │
                    └──────────┬──────────┘
                               │ ontology:validate
                               ▼
                    ┌─────────────────────┐
                    │       SHACL         │
                    │ Quality Gate        │
                    └──────────┬──────────┘
                               │ valid RDF / sync
                               ▼
                    ┌─────────────────────┐
                    │      GraphDB        │
                    │ World Model Runtime │
                    └─────────────────────┘
```

### 3.2 Ontology as Code 目录

Git 策展面：`ontology/`（实体、词典、claims、映射、catalog、抽取配置、样例、Protégé/SHACL 入口）。

| 子目录 | 用途 |
|---|---|
| `ontology/catalog/` | 文献/检索 ENT 目录（`HMD:ENT:*`） |
| `ontology/entities/` | 金路径企业实体 |
| `ontology/dictionary/` | ER 企业词典 |
| `ontology/mappings/` | BIOS / ChEBI / BERN2 type / Zingg |
| `ontology/claims/` | 策展 KnowledgeClaim |
| `ontology/extract/` | 表格指标等抽取配置（湖侧） |
| `ontology/examples/` | Golden Path 样例包 |
| `ontology/owl/` | Protégé 入口说明（权威 OWL 在 `schema/generated/`） |
| `ontology/shapes/` | SHACL 入口说明（权威 shapes 在 `schema/`） |

LinkML SSOT 仍在 `schema/`；生成物在 `src/biomed_ontology/_generated/`。可选 `task ontology:sync-artifacts` 复制 OWL/SHACL 到 `ontology/`。

**策展资产全地图、sync 矩阵、REST/MCP 接线、BERN2/BIOS**：见 [策展资产与运行时机制](curation-and-runtime.md)。

### 3.3 PR / 运行时流程

```text
Git → PR → task ontology:validate
         →（可选 Protégé 审阅生成 OWL）
         → merge
         → hmd foundation sync
              ├── GraphDB   (ontology / knowledge / provenance)
              ├── Milvus    (foundation_evidence)
              └── OpenMetadata (Glossary HMDEnterpriseAssets)
         → Semantic Ops / golden 只读三后端
```

`ontology:validate` 检查：目录结构、映射对齐、Golden Path 实体可达。

### 3.4 三层 ID（toolchain 视角）

```text
① Enterprise Entity   HMD:ENT:{DC|TGT|IND|…}:slug
② External Concept    BIOS:… / CHEBI:… / HGNC:…
③ Evidence            pubmed:… | patent:… | eln:… | ev:…
```

```text
Enterprise Entity
        │
        ├── skos:exactMatch → External Concept
        │
        └── KnowledgeClaim.evidence_ids → Evidence
```

Graph / Milvus / API 主键仍是 **Enterprise ID**。详见 [Foundation](../architecture/foundation.md)、[LinkML](../architecture/linkml.md)。

### 3.5 关键命令与模块

```bash
task gen                      # LinkML → pydantic / JSON Schema / SHACL / OWL
task ontology:validate        # 目录 + 映射 + Golden Path
task ontology:sync-artifacts  # 可选：复制 OWL/SHACL
uv run hmd foundation sync    # 策展 YAML → 三后端
```

| 模块 | 路径 |
|---|---|
| RDF 装载 | `ontology/rdf.py`（`GraphStore`） |
| Foundation sync | `foundation/sync.py` |
| SHACL 闸门 | `quality/` |
| GraphDB 客户端 | `foundation/graphdb.py` |
| Clique / SSSOM | `ontology/clique.py` |

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| LinkML 唯一 SSOT | Protégé / OWL 不得回写 schema |
| validate 先于 sync | SHACL 失败不应入图 |
| sync 不清 extracted 图 | 湖侧 claim 与 seed provenance 分离 |
| 图引擎只认 GraphDB | 避免第二套 RDF 服务运维面 |
| Golden Path 可机检 | `ontology:validate` 绑定 HMPL-504 链路 |

| 失败模式 | 处理 |
|---|---|
| gen 未跑就改消费方 | CI diff `_generated/` |
| 手写 TTL 绕过 validate | 入图形状漂移 |
| 双 SSOT（schema + Protégé） | 合并冲突，Semantic Ops 不一致 |

---

## 5. 如何验证

```bash
task gen
task ontology:validate
uv run hmd foundation sync
uv run hmd foundation golden-eval --compact
uv run pytest tests/test_clique.py tests/test_ontology_validate.py -q 2>/dev/null || true
```

相关：[Golden Path](golden-path.md)、[企业身份与目录 SSOT](seed.md)、[RDF](rdf.md)。
