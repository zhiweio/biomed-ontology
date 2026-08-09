# Ontology as Code（策展面 · 分层 SSOT）

本目录是 **Git 策展 SSOT**：企业实体、ER 词典、知识断言、映射表、文献 catalog、抽取配置、Protégé 入口、Golden 样例。

| 层 | 目录 | 角色 |
|---|---|---|
| **Schema SSOT** | [`schema/`](../schema/) | LinkML 唯一模式/契约（**不**放在本目录） |
| **Ontology 策展** | **本目录** | `HMD:ENT:*` 实体、Dictionary、claims、mappings、catalog |
| **运维内容** | [`data/`](../data/) | 文献 corpus、gold、releases、evidence/assets 投影、缓存 |
| **运行时** | GraphDB + Milvus + OM | sync 后查询只认后端（禁止 YAML fallback） |

**不要**把 `data/corpus` / `data/gold` 整仓搬进本目录。  
文献 ENT 目录 SSOT：[`catalog/`](catalog/)；ER 别名：[`dictionary/`](dictionary/)。

机制全景（子目录地图、sync、REST/MCP、BERN2/BIOS）：  
[`docs/ontology/curation-and-runtime.md`](../docs/ontology/curation-and-runtime.md)。

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
├── entities/      # enterprise_entities.yaml — 金路径企业实体
├── dictionary/    # enterprise_dictionary.yaml — ER Exact
├── claims/        # knowledge_claims.yaml
├── mappings/      # BIOS / BERN2 / ChEBI / zingg_matches.jsonl
├── catalog/       # diseases / substances / targets / ambiguity
├── extract/       # table_metrics.yaml — 表格指标列映射
├── owl/           # Protégé 入口说明（非 SSOT）
├── shapes/        # SHACL 入口说明（非 SSOT）
└── examples/
    └── golden_path/hmpl504/
```

运行投影（evidence / assets / BIOS 子集）仍在 [`data/foundation/`](../data/foundation/)。

## 变更流程

```text
Edit ontology/entities|dictionary|claims|mappings|catalog|extract
  → task ontology:validate → hmd foundation sync

Edit schema/*.yaml → task gen → PR
```

详述见 [`docs/ontology/toolchain.md`](../docs/ontology/toolchain.md) 与  
[`docs/ontology/curation-and-runtime.md`](../docs/ontology/curation-and-runtime.md)。
