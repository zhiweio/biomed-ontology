# biomed-ontology 手册

**AI-Ready Scientific Data Foundation for Drug Discovery**  
面向创新药研发的 **AI 原生科研数据基座**。

用企业内部实体 ID（`HMD:ENT:*`）锚定研发对象，挂上关系、证据与内部数据资产，经
`hmd serve`（MCP/REST）把治理过的 **Context Pack** 交给仓外 Agent。BIOS / UMLS 等公共知识
只做 xref，不是企业主键。不要把本仓库称作「企业知识图谱项目」——GraphDB 是六层栈之一。

> BIOS provides the biomedical world. Enterprise Ontology provides the company's world.

本仓库**不做 Agent 编排、发现应用或组学平台**。交付的是可查询的语义世界，以及其上的
**Ontology Semantic Layer** 与 **Data-for-Agent 契约**。

手册目标：让接手的人能**改、能证伪、能扩展**——读懂设计与实现边界，而不是复制一段命令。

## 这本手册怎么读

每一章按同一骨架写：

1. **为什么存在** — 要解决的业务或系统问题
2. **设计取舍** — 选了什么、明确放弃了什么
3. **设计与实现** — 模块边界、数据流、关键配置/符号名与调用链（用路径说明，不贴源码）
4. **不变量与失败模式** — 哪些事不能静默发生
5. **如何验证** — 相关测试名与 bash 命令

读完一章，应能回答：「如果我改 X，哪条不变量会碎，评测哪一臂会告诉我。」

## 读者路径

| 你是谁 | 建议阅读顺序 |
|---|---|
| 第一次接触本仓库 | [快速开始](getting-started.md) → [分层与产品栈](architecture/layers.md) → [Foundation](architecture/foundation.md) → [设计不变量](invariants.md) |
| 要做企业世界模型 | [Foundation](architecture/foundation.md) → [IdentityService](ontology/identity.md) → [Golden Path](ontology/golden-path.md) → `hmd foundation golden` |
| 理解策展 / sync / ER | [策展资产与运行时](ontology/curation-and-runtime.md) → [IdentityService](ontology/identity.md) → [演进闭环](evolution/loop.md) |
| 要改检索 / 本体 | [类型化链接](ontology/links.md) → [三通道与 RRF](retrieval/hybrid.md) → [查询改写 vs 图通道](retrieval/ontology-paths.md) → [评测消融](eval/arms.md) |
| 要接仓外 Agent | [Data-for-Agent](architecture/data-for-agent.md) → [Semantic Access](tools/tools.md) → [Citationware](tools/citationware.md) → [serve](tools/serve.md) |
| 要接入湖 / Evidence | [Document Lake](architecture/document-pipeline.md) → [IngestQA](parse/ingest-qa.md) → [Router](parse/router.md) → [Milvus](retrieval/milvus.md) |
| 要理解抽取 / 接地 | [事实抽取](ontology/extract.md) → [归一化](ontology/normalize.md) → [策展](ontology/curation-and-runtime.md) |
| 合规 / 采购 | [Tier 矩阵](licensing/tiers.md) → [组件闸门](licensing/components.md) → [NOTICE](appendix/notice.md) |

## 与 README 的分工

!!! info "数字与命令以 README 为准"
    **命令、安装、实测消融表、显著性数字**只维护在仓库根目录的 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
    那些数字有 `tests/test_readme.py` 守着 —— 手册抄表只会腐烂成第二份谎言。

    本手册讲的是**机制、不变量与读数方法**。需要引用性能结论时，链到 README，不要把表复制过来。

## 一句话定位

**AI-Ready Scientific Data Foundation**：把公共生物医学知识与企业研发数据统一成可查询、
可追溯、可治理的语义世界。仓外 Agent 只吃四种数据形态——Document / Evidence / Claim / Context Pack——
经 `hmd serve` 暴露，而不是直接打湖表、SPARQL 或裸向量 API。

业界六层栈（Lakehouse · Metadata Catalog · Scientific KG · Evidence Index · Ontology Services · AI Context APIs）
分别落在 Iceberg/MinIO/Trino、OpenMetadata、GraphDB、Milvus、LinkML/IdentityService、`hmd serve`。
仓内实现编号是 L0–L8，见 [分层与产品栈](architecture/layers.md)。

**FAIR**：Findable（稳定 CURIE / Evidence ID）· Accessible（许可在候选期过滤）·
Interoperable（LinkML/RDF/SSSOM，外部标准只 xref）· Reusable（PROV + `ontology_release_id`）。

**Scientific Data Loop**：研究系统 → 语义基座 → AI 消费 → 科学家审校 → 知识回写 Git → sync。
闭环产品是企业科学知识层变厚，不是模型权重更新。

## 本地预览手册

```bash
uv sync --extra docs --extra dev
task docs:serve    # http://127.0.0.1:8000
task docs          # 严格构建（断链会失败）
```
