# NOTICE / 出处摘要

完整法律文本与修改列表见仓库根目录 [NOTICE](https://github.com/zhiweio/biomed-ontology/blob/main/NOTICE)。本页只帮开发者建立「义务在哪执行」的心智模型。

## 两面义务

| 面 | 管什么 | 执行点 |
|---|---|---|
| 数据 | Tier 0–3 源 | `licensing.POLICIES`、named graph、LicenseScope |
| 软件/模型 | 第三方组件 | `licensing.COMPONENTS`、`assert_component_cleared`、NOTICE 正文 |

## 已 cleared 示例

**Ontos-AI/knowhere**（Apache-2.0）：解析路径衍生自该项目，须保留许可声明并按 §4(b) 标注修改 —— README 与 NOTICE 均指向此处。

## Pending（待法务核实）

| 组件 | 风险摘要 |
|---|---|
| PyMuPDF | AGPL / 需商业许可对外服务的可能 |
| MinerU | 附加商业门槛 + 在线服务标示义务 |
| BiomedCLIP | MIT 权重 + 模型卡「任何部署用途超出范围」 |

`review=pending` 时默认抛 `LicenseViolation`；本地显式 accept 见 [组件闸门](../licensing/components.md)。

## 开发者检查

- 新增第三方：登记 `COMPONENTS` + NOTICE 段落，不要只改 requirements  
- 新增数据源：registry + tier + 命名图，不要只丢进 corpus  
- README 不得把 pending 说成已 clear（有测试绊线）  
