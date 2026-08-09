# 组件闸门

源码：`licensing.COMPONENTS` + `assert_component_cleared`（`src/biomed_ontology/licensing.py`）。

## 为什么存在

[Tier 矩阵](tiers.md) 管**数据**能否被看见、导出、训练；本模块管**第三方软件与模型**能否被加载。二者问题域不同：

- 数据源有实体类型、权威范围、命名图；
- 解析器有 AGPL、双授权、附加商业条款；
- 模型权重有 MIT 许可证之外、模型卡上的**用途限定**。

若只把义务写在 NOTICE 里，只有写它的人知道；写成**启动 / 调用时抛异常**，换人接手也绕不过去。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 登记形态 | `ComponentObligation`（license_id、obligation 文案、review 状态） | 与数据 tier 混表 |
| 未结论组件 | `review=pending` → 默认 `LicenseViolation` | 无声启用 |
| PoC 便利 | `HMD_ACCEPT_UNCLEARED_COMPONENTS=true`（默认）+ 启动 `warnings()` | 生产默认可用 |
| 生产 | `HMD_ACCEPT_UNCLEARED_COMPONENTS=false` | 带 pending 组件上线 |
| cleared 标准 | 法务改 `review` 为 `cleared` | README 口头「已核实」 |

## 设计与实现

### 当前登记（`COMPONENTS`）

| ID | 许可要点 | review |
|---|---|---|
| `knowhere` | Apache-2.0，保留声明并标注修改 | cleared |
| `pymupdf4llm` | AGPL / 商业双授权（底层 PyMuPDF） | **pending** |
| `docling` | MIT | **pending** |
| `mineru` | Apache-2.0 + 附加商业门槛与标示义务 | **pending** |
| `biomedclip` | MIT 权重 + 模型卡「任何部署用途超出范围」 | **pending** |

!!! warning "MIT 看起来最干净的时候"
    BiomedCLIP 的用途限定独立于版权许可。只记 MIT 会让依赖清单显得干干净净，而真正风险在模型卡。README 必须出现「待法务核实」。

### 闸门行为

```text
assert_component_cleared(component_id, accept_uncleared=…):
  cleared / not_required → 放行
  pending + accept_uncleared=true → 放行（启动 warnings 留痕）
  pending + accept_uncleared=false → LicenseViolation
```

`uncleared_components()` 列出仍 pending 的条目，供启动日志与运维检查。

### 调用点（示例）

| 组件 ID | 触发路径 |
|---|---|
| `pymupdf4llm` | PyMuPDF4LLM 布局解析、`parse/layout/pymupdf4llm.py` |
| `docling` | Docling 布局路径 |
| `mineru` | MinerU 本地 import 或 HTTP 路由 |
| `biomedclip` | 多模态 / 视觉列嵌入、`embed` 相关路径 |
| `knowhere` | 衍生自 Ontos-AI/knowhere 的解析逻辑（已 cleared） |

`hmd index` / `hmd eval` 在 PoC 默认 `accept_uncleared_components=true`，可直接跑通链路；**生产务必关闭**。

### NOTICE 双面义务

见 [附录 · NOTICE](../appendix/notice.md) 与仓库根目录 `NOTICE`：

- 对 knowhere 的 Apache §4(b) 修改标注；
- 对 pending 组件的风险提示与「待法务核实」声明。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 新依赖必须登记 `COMPONENTS` + NOTICE | 义务不可执行、README 绊线失败 |
| pending 不得在生产默认启用 | AGPL / 用途限定风险进生产 |
| `biomedclip` 不得仅记 MIT | 低估部署限制 |
| cleared 须有法务改 `review` 字段 | 虚假「已核实」 |

失败模式：

- **本地能跑、生产启动失败**：预期；需法务结论或换非 AGPL 路径。
- **只改 requirements 不登记**：闸门未触发，合规洞。
- **把 pending 当 cleared 写 README**：`test_readme` 等绊线拦截。

## 如何验证

```bash
uv run pytest tests/test_licensing.py -q
# README 绊线：MinerU + PyMuPDF4LLM + BiomedCLIP + 待法务核实
HMD_ACCEPT_UNCLEARED_COMPONENTS=false uv run hmd index --help  # 预期 pending 组件路径抛错
```

数据 tier 见 [tiers](tiers.md)。
