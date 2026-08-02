# biomed-ontology 手册

面向阿斯利华创新药研发场景的**生物医药语义层数据基座** PoC。

本仓库不交付 AI agent 本身 —— 它交付的是 agent **必须先存在**的那一层：谁是谁（本体）、发生了什么（事实）、在哪说的（可溯源检索）、多可信（质量与许可）。手册的目标不是列命令，而是让接手的人能**改、能证伪、能扩展**。

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
| 第一次接触本仓库 | [快速开始](getting-started.md) → [分层架构](architecture/layers.md) → [端到端数据流](architecture/pipeline.md) → [设计不变量](invariants.md) |
| 要改检索 / 本体 | [类型化链接](ontology/links.md) → [三通道与 RRF](retrieval/hybrid.md) → [查询改写 vs 图通道](retrieval/ontology-paths.md) → [评测消融](eval/arms.md) |
| 要接 agent / 对外服务 | [11 工具](agent/tools.md) → [Citationware](agent/citationware.md) → [许可](licensing/tiers.md) |
| 要接视觉 / Milvus | [资产路径](parse/assets.md) → [五列](retrieval/milvus.md) → [图型](parse/figure-type.md) → [嵌入器](retrieval/embedders.md) |
| 合规 / 采购 | [Tier 矩阵](licensing/tiers.md) → [组件闸门](licensing/components.md) → [NOTICE](appendix/notice.md) |

## 与 README 的分工

!!! info "数字与命令以 README 为准"
    **命令、安装、实测消融表、显著性数字**只维护在仓库根目录的 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
    那些数字有 `tests/test_readme.py` 守着 —— 手册抄表只会腐烂成第二份谎言。

    本手册讲的是**机制、不变量、事故教训与读数方法**。需要引用性能结论时，链到 README，不要把表复制过来。

## 一句话定位

把本体从「术语表」做成**可遍历、可索引、带治理的操作层**。在创新药研发这个垂类里，这意味着：

- 药 ↔ 靶点 ↔ 适应症的类型化链接能在**检索期**走通（不只在图谱浏览器里好看）  
- 许可边界在**候选生成阶段**就生效（不是返回前裁剪）  
- 引用能还原到原文语境（Citationware）  
- 「没达成」有署名豁免，而不是悄悄调低阈值  

## 本地预览手册

```bash
uv sync --extra docs --extra dev
make docs-serve    # http://127.0.0.1:8000
make docs          # 严格构建（断链会失败）
```
