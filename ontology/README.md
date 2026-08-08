# Ontology as Code（策展面 · 分层 SSOT）

本目录是 **Git 策展 SSOT**：企业实体、ER 词典、知识断言、映射表、Protégé 入口、Golden 样例。

| 层 | 目录 | 角色 |
|---|---|---|
| **Schema SSOT** | [`schema/`](../schema/) | LinkML 唯一模式/契约（**不**放在本目录） |
| **Ontology 策展** | **本目录** | `HMD:ENT:*` 实体、Dictionary、claims、mappings |
| **运维内容** | [`data/`](../data/) | 文献 corpus、gold、releases、evidence/assets 投影、缓存 |
| **运行时** | GraphDB + Milvus + OM | sync 后查询只认后端（禁止 YAML fallback） |

**不要**把 `data/corpus` / `data/gold` 整仓搬进本目录。  
**已退役** [`data/seed/`](../data/seed/)（手工 `HMD:SUB`）；别名迁入 [`dictionary/`](dictionary/)。

## 身份：Entity Resolution Pipeline

```text
Text → BERN2 (NER/NEN)
     → Enterprise Dictionary (Exact)
     → Zingg matches (Cross-source)
     → BIOS / ChEBI / HGNC / … (Ontology Mapping)
     → HMD:ENT:*
```

## 目录

```text
ontology/
├── entities/      # enterprise_entities.yaml
├── dictionary/    # enterprise_dictionary.yaml
├── claims/        # knowledge_claims.yaml
├── mappings/      # BIOS / BERN2 / ChEBI / zingg_matches.jsonl
├── owl/           # Protégé 入口说明
├── shapes/        # SHACL 入口说明
└── examples/
    └── golden_path/hmpl504/
```

运行投影（evidence / assets / BIOS 子集）仍在 [`data/foundation/`](../data/foundation/)。

## 变更流程

```text
Edit ontology/entities|dictionary|claims  →  task ontology:validate  →  hmd foundation sync
Edit schema/*.yaml                        →  task gen               →  PR
```

详述见 [`docs/ontology/toolchain.md`](../docs/ontology/toolchain.md)。
