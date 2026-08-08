# biomed-ontology 手册

面向阿斯利华创新药研发的 **Enterprise Biomedical World Model / AI Data Foundation**。

用企业内部实体 ID（`HMD:ENT:*`）锚定研发对象，挂上关系、证据与内部数据资产，经
`hmd serve`（MCP/REST）供仓外 Agent 调用。BIOS 等公共知识是挂靠层，不是企业主键。

> BIOS provides the biomedical world. Enterprise Ontology provides the company's world.

本仓库**不做 Agent 编排**。交付的是可查询的语义世界，以及其上的 **Ontology Semantic Layer**：

术语与身份 · 层级扩展 · 类型化关系 · 外部挂靠 · 结构化事实 · 证据检索 · Citationware ·
企业资产定位 · 许可边界 · 可观测与演进 · LinkML/SHACL 治理。

手册的目标不是列命令，而是让接手的人能**改、能证伪、能扩展**。

## 这本手册怎么读

每一章尽量按同一骨架写：

1. **为什么存在** —— 业务问题或曾经踩过的坑  
2. **设计取舍** —— 为什么选这条路，放弃了什么  
3. **实现走读** —— 关键类型、调用链、常量，落到真实路径  
4. **不变量与事故** —— 哪些事不能静默发生  
5. **如何验证** —— 相关测试 / CLI  

读完一章，你应当能回答：「如果我改 X，哪条不变量会碎，评测哪一臂会告诉我。」

## 读者路径

| 你是谁 | 建议阅读顺序 |
|---|---|
| 第一次接触本仓库 | [快速开始](getting-started.md) → [Foundation](architecture/foundation.md) → [分层架构](architecture/layers.md) → [设计不变量](invariants.md) |
| 要做企业世界模型 / Foundation | [Foundation 架构](architecture/foundation.md) → [Toolchain](ontology/toolchain.md) → [Golden Path](ontology/golden-path.md) → `hmd foundation golden` |
| 要改检索 / 本体 | [类型化链接](ontology/links.md) → [三通道与 RRF](retrieval/hybrid.md) → [查询改写 vs 图通道](retrieval/ontology-paths.md) → [评测消融](eval/arms.md) |
| 要接 Semantic Access | [Semantic Access](tools/tools.md) → [Citationware](tools/citationware.md) → [serve](tools/serve.md) → [许可](licensing/tiers.md) |
| 要接视觉 / Evidence Index | [资产路径](parse/assets.md) → [Milvus](retrieval/milvus.md) → [图型](parse/figure-type.md) → [嵌入器](retrieval/embedders.md) |
| 合规 / 采购 | [Tier 矩阵](licensing/tiers.md) → [组件闸门](licensing/components.md) → [NOTICE](appendix/notice.md) → [BIOS 闸门](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md) |

## 与 README 的分工

!!! info "数字与命令以 README 为准"
    **命令、安装、实测消融表、显著性数字**只维护在仓库根目录的 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
    那些数字有 `tests/test_readme.py` 守着 —— 手册抄表只会腐烂成第二份谎言。

    本手册讲的是**机制、不变量、事故教训与读数方法**。需要引用性能结论时，链到 README，不要把表复制过来。

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
