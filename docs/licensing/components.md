# 组件闸门

源码：`licensing.COMPONENTS` + `assert_component_cleared`。

## 数据许可 ≠ 软件许可

上面的 Tier 矩阵管**数据**；`COMPONENTS` 管**第三方软件/模型**。混进同一张表会失去：实体类型、权威范围、命名图等数据侧概念，也会让「解析器 AGPL」和「DrugBank 订阅」搅在一起无法执行。

法务义务如果只写在 NOTICE 里，只有写它的人知道。写成**启动/调用时抛异常**，换人接手也绕不过去。

## 当前登记

| ID | 许可要点 | review |
|---|---|---|
| `knowhere` | Apache-2.0，保留声明并标注修改 | cleared |
| `pymupdf` | AGPL / 商业双授权 | **pending** |
| `mineru` | Apache-2.0 + 附加商业门槛与标示义务 | **pending** |
| `biomedclip` | MIT 权重 + 模型卡「任何部署用途超出范围」 | **pending** |

!!! warning "MIT 看起来最干净的时候"
    BiomedCLIP 的用途限定独立于版权许可。只记 MIT 会让依赖清单显得干干净净，而真正风险在模型卡。README 必须出现「待法务核实」。

## 闸门行为

```text
assert_component_cleared(id):
  cleared / not_required → 放行
  pending + accept_uncleared → 放行（仅显式配置，启动告警留痕）
  pending → LicenseViolation
```

`HMD_ACCEPT_UNCLEARED_COMPONENTS=true` 允许本地试用，**不允许**无声带进生产。

调用点示例：PyMuPDF 渲染路径、MinerU 客户端、BiomedCLIP 嵌入/图型。

## NOTICE 双面义务

见 [附录 · NOTICE](../appendix/notice.md) 与仓库根目录 `NOTICE`：既有对 knowhere 的 Apache §4(b) 修改标注，也有 pending 组件的风险提示。

## 如何验证

```bash
uv run pytest tests/test_licensing.py -q
# README 绊线：MinerU + PyMuPDF + BiomedCLIP + 待法务核实
```
