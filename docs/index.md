# biomed-ontology 手册

面向阿斯利华创新药研发的 **Enterprise Biomedical World Model / AI Data Foundation**。

用企业内部实体 ID（`HMD:ENT:*`）锚定研发对象，挂上关系、证据与内部数据资产，经
`hmd serve`（MCP/REST）供仓外 Agent 调用。BIOS 等公共知识是挂靠层，不是企业主键。

> BIOS provides the biomedical world. Enterprise Ontology provides the company's world.

本仓库**不做 Agent 编排**。交付的是可查询的语义世界，以及其上的 **Ontology Semantic Layer**：

术语与身份 · 层级扩展 · 类型化关系 · 外部挂靠 · 结构化事实 · 证据检索 · Citationware ·
企业资产定位 · 许可边界 · 可观测与演进 · LinkML/SHACL 治理。

手册目标：让接手的人能**改、能证伪、能扩展**——读懂设计与实现边界，而不是复制一段命令。

## 这本手册怎么读

每一章按同一骨架写（不写改造日记）：

1. **为什么存在** — 要解决的业务或系统问题  
2. **设计取舍** — 选了什么、明确放弃了什么  
3. **设计与实现** — 模块边界、数据流、关键配置/符号名与调用链（用路径说明，不贴源码）  
4. **不变量与失败模式** — 哪些事不能静默发生  
5. **如何验证** — 相关测试名与 bash 命令  

读完一章，应能回答：「如果我改 X，哪条不变量会碎，评测哪一臂会告诉我。」

## 读者路径

| 你是谁 | 建议阅读顺序 |
|---|---|
| 第一次接触本仓库 | [快速开始](getting-started.md) → [Foundation](architecture/foundation.md) → [分层架构](architecture/layers.md) → [设计不变量](invariants.md) |
| 要做企业世界模型 / Foundation | [Foundation](architecture/foundation.md) → [Toolchain](ontology/toolchain.md) → [Golden Path](ontology/golden-path.md) → `hmd foundation golden` |
| 理解策展 / sync / ER·BIOS | [策展资产与运行时机制](ontology/curation-and-runtime.md) → [Foundation](architecture/foundation.md) → [演进闭环](evolution/loop.md) |
| 要改检索 / 本体 | [类型化链接](ontology/links.md) → [三通道与 RRF](retrieval/hybrid.md) → [查询改写 vs 图通道](retrieval/ontology-paths.md) → [评测消融](eval/arms.md) |
| 要接 Semantic Access | [Semantic Access](tools/tools.md) → [Citationware](tools/citationware.md) → [serve](tools/serve.md) → [许可](licensing/tiers.md) |
| 要接文档解析 / Evidence Index | [Router](parse/router.md) → [版面](parse/layout.md) → [切片](parse/chunks.md) → [Document Pipeline](architecture/document-pipeline.md) → [Milvus](retrieval/milvus.md) |
| 要理解抽取 / 接地 / 审校写回 | [Document Pipeline](architecture/document-pipeline.md) → [事实抽取 TriModal](ontology/extract.md) → [Normalizer](ontology/normalize.md) → [策展](ontology/curation-and-runtime.md) → [演进](evolution/loop.md) |
| 合规 / 采购 | [Tier 矩阵](licensing/tiers.md) → [组件闸门](licensing/components.md) → [NOTICE](appendix/notice.md) |

## 与 README 的分工

!!! info "数字与命令以 README 为准"
    **命令、安装、实测消融表、显著性数字**只维护在仓库根目录的 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
    那些数字有 `tests/test_readme.py` 守着 —— 手册抄表只会腐烂成第二份谎言。

    本手册讲的是**机制、不变量与读数方法**。需要引用性能结论时，链到 README，不要把表复制过来。

## 一句话定位

**Enterprise Biomedical World Model**：把公共生物医学知识与企业研发数据统一成可查询、
可追溯、可治理的语义世界；Semantic Access 暴露完整 Ontology Semantic Layer
（身份、层级、关系、事实、证据、引用、资产、许可与演进），供仓外 Agent 使用——不是 chatbot，
也不是「加了别名的检索引擎」。

## 本地预览手册

```bash
uv sync --extra docs --extra dev
task docs:serve    # http://127.0.0.1:8000
task docs          # 严格构建（断链会失败）
```
