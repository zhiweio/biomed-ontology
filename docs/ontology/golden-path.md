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

## Semantic Access 入口

运行时强制读 **GraphDB + Milvus + OpenMetadata**（禁止 YAML fallback）。  
`data/foundation/*.yaml` 仅离线种子 → `ontology:validate` → `hmd foundation sync`（幂等）入库。

```bash
uv run hmd foundation sync
uv run hmd foundation golden --candidate HMPL-504          # Rich 分步
uv run hmd foundation golden --candidate HMPL-504 --json   # 机器可读
uv run hmd foundation golden-eval                          # 多路径 Rich
uv run hmd foundation golden-eval --compact                # 仅 Suite 表
uv run hmd foundation golden-eval --json                   # 多路径 JSON
uv run hmd serve --mcp                                     # 单一 REST + MCP
```

评估检查：`backends` 无 yaml；BIOS 桥接读 GraphDB `graph/biomedical`；证据 Milvus；资产 OpenMetadata。  
检索操作 structlog 四支柱：`Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)`。

MCP / REST 主契约：`get_entity_context`（不暴露裸 `graph_sparql` / 向量 API）。

## 最终视觉

```text
                    仓外 LLM / Tool Client
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
