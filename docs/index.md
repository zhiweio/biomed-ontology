# biomed-ontology 手册

面向阿斯利华创新药研发场景的**企业级 AI Data Foundation / 生物医药语义层** PoC。

本仓库不交付 AI agent 本身 —— 它交付两条底座：

1. **检索与 Citationware**（既有 L0–L8）：谁是谁、发生了什么、在哪说的、多可信  
2. **Foundation 世界模型**：Enterprise Ontology（`HMD:ENT:*`）+ GraphDB + Milvus Evidence Index + OpenMetadata  

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
| 第一次接触本仓库 | [快速开始](getting-started.md) → [分层架构](architecture/layers.md) → [Foundation](architecture/foundation.md) → [设计不变量](invariants.md) |
| 要做企业世界模型 / Foundation | [Foundation 架构](architecture/foundation.md) → [Toolchain](ontology/toolchain.md) → [Golden Path](ontology/golden-path.md) → `hmd foundation golden` |
| 要改检索 / 本体 | [类型化链接](ontology/links.md) → [三通道与 RRF](retrieval/hybrid.md) → [查询改写 vs 图通道](retrieval/ontology-paths.md) → [评测消融](eval/arms.md) |
| 要接 agent / 对外服务 | [11 工具](agent/tools.md) + Foundation Semantic Ops → [Citationware](agent/citationware.md) → [许可](licensing/tiers.md) |
| 要接视觉 / Evidence Index | [资产路径](parse/assets.md) → [Milvus](retrieval/milvus.md) → [图型](parse/figure-type.md) → [嵌入器](retrieval/embedders.md) |
| 合规 / 采购 | [Tier 矩阵](licensing/tiers.md) → [组件闸门](licensing/components.md) → [NOTICE](appendix/notice.md) → [BIOS 闸门](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md) |

## 与 README 的分工

!!! info "数字与命令以 README 为准"
    **命令、安装、实测消融表、显著性数字**只维护在仓库根目录的 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
    那些数字有 `tests/test_readme.py` 守着 —— 手册抄表只会腐烂成第二份谎言。

    本手册讲的是**机制、不变量、事故教训与读数方法**。需要引用性能结论时，链到 README，不要把表复制过来。

## 一句话定位

把本体从「术语表」做成**可遍历、可索引、带治理的企业世界模型**。在创新药研发这个垂类里，这意味着：

- **Enterprise ID** 是对外锚点；BIOS / ChEBI 等是外部概念  
- 药 ↔ 靶点 ↔ 适应症的类型化链接能在**检索期与 GraphDB** 走通  
- GraphDB / Milvus / OpenMetadata 分司关系、证据、企业资产  
- 许可边界在**候选生成阶段**就生效；BIOS 全量需显式 ACK  
- 引用能还原到原文语境（Citationware / Evidence Index）  
- Ontology Evolution 一期只落 KGCL 候选，不自动改本体  

## 本地预览手册

```bash
uv sync --extra docs --extra dev
task docs:serve    # http://127.0.0.1:8000
task docs          # 严格构建（断链会失败）
```
