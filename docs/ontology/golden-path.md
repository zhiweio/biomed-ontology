# Golden Path

第一条可演示链路（不要一次做 100 个 class）：

```text
候选药物 → Target → Disease → Evidence → 企业 Asset
```

种子实体：**HMPL-504 / savolitinib**（`HMD:ENT:DC:savolitinib`）。
文档中的示意代号 `ABC-001` 映射到该实体。

样例包：[`ontology/examples/golden_path/hmpl504/`](../../ontology/examples/golden_path/hmpl504/)。

## 用户问题

> Candidate X 的作用靶点是什么？它主要针对哪些疾病？有哪些 PubMed / Patent 证据？企业内部有哪些 ELN / LIMS 数据支持这些结论？

## 步骤

1. **定义候选药** — `HMD:ENT:DC:savolitinib`，`exactMatch` → BIOS / ChEBI / DrugBank
2. **BERN2 / 词典识别** — `HMPL-504` / `MET` / `NSCLC` → Resolver → Enterprise ID
3. **Target 关系** — `targets` → `HMD:ENT:TGT:MET`
4. **Disease 关系** — `investigates` → `HMD:ENT:IND:nsclc`；靶点 `associatedWith` 适应症
5. **PubMed Evidence** — Claim + span + `pubmed:` / `ev:lit:*`
6. **Patent Evidence** — 复用同一 pipeline（`patent:` / `ev:pat:*`）
7. **ELN / LIMS** — `testedIn` Experiment、`hasAssay` Assay + OpenMetadata 资产投影

## Citationware

不要只存 `PMID → 实体`。第一版即形成：

```text
Claim
 ├── subject / predicate / object
 ├── evidence_ids
 ├── span
 └── confidence
```

`get_entity_context` 聚合返回 `entity` / `targets` / `diseases` / `evidence` / `internal_assets`。

## Agent 入口

```bash
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation serve --mcp   # :8100
```

MCP / REST 主契约：`get_entity_context`（不暴露裸 `graph_sparql` / 向量 API）。

## 最终视觉

```text
                         AI Agent
                            │
                            ▼
                 get_entity_context()
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
           GraphDB        Milvus      OpenMetadata
          World Model    Evidence      Assets
              │             │             │
              └──────┬──────┘             │
                     ▼                    │
              HMPL-504 / savolitinib ◄────┘
                     │
                  targets
                     ▼
                    MET
                     │
              associatedWith
                     ▼
                  NSCLC
                     │
         PubMed / Patent / ELN / LIMS
```

核心类（第一版）：DrugCandidate、Target、Indication、Program、Experiment、Assay、Publication、Evidence（claim）、以及必要的 Compound / Biomarker 槽位。
