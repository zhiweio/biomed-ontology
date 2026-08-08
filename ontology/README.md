# Ontology as Code（策展面）

本目录是 **Git 策展面**：映射表、Protégé 审阅入口、Golden Path 样例。

**LinkML 仍是唯一 SSOT**，位于仓库根 [`schema/`](../schema/)。不要在这里另开一份
「手写 OWL 真相源」。

## 工具链职责（A1）

| 组件 | 职责 | 落点 |
|---|---|---|
| **LinkML** | Enterprise Domain Schema / Canonical Model（唯一 SSOT） | `schema/*.yaml` |
| **Protégé** | 审阅生成 OWL；**禁止**回写为第二真相源 | 打开 `schema/generated/*.owl.ttl`（见 [`owl/`](owl/)） |
| **rdflib / pyshacl** | RDF 工程层（parse / transform / SHACL；**不引入 Jena**） | `src/biomed_ontology/ontology/rdf.py`、`quality/`、`foundation/sync.py` |
| **SHACL** | 入图质量门 | `schema/generated/*.shacl.ttl` + `schema/shapes/` |
| **GraphDB** | World Model Runtime | `docker/docker-compose.foundation.yml` |

```text
Protégé (review) ──read──► schema/generated/*.owl.ttl
                              ▲
LinkML SSOT (schema/) ──task gen──┘
                              │
                    rdflib + pyshacl
                              │
                           SHACL gate
                              ▼
                           GraphDB
```

## 目录

```text
ontology/
├── owl/           # Protégé 入口说明（生成物在 schema/generated）
├── shapes/        # SHACL 入口说明
├── mappings/      # BIOS / BERN2 / ChEBI 等可审映射
└── examples/
    └── golden_path/hmpl504/
```

## 三层 ID

1. **Enterprise Entity** — `HMD:ENT:{DC|TGT|IND|…}:slug`
2. **External Concept** — `BIOS:` / `CHEBI:` / `HGNC:` / …
3. **Evidence** — `pubmed:` / `patent:` / `eln:` / `lims:` / `ev:`

Graph / Milvus / API 主键仍是 Enterprise ID；Evidence ID 只锚定出处。

## 变更流程

```text
Edit schema/*.yaml  →  task gen  →  PR
       │
       ├── Ontology / SHACL checks (task ontology:validate)
       └── Review generated OWL in Protégé (optional)
```

详述见 [`docs/ontology/toolchain.md`](../docs/ontology/toolchain.md)。
