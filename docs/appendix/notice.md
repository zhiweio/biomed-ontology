# NOTICE / 出处摘要

完整法律文本与修改列表见仓库根目录 [NOTICE](https://github.com/zhiweio/biomed-ontology/blob/main/NOTICE)。  
本页建立「义务写在哪儿」与「义务在哪儿执行」的心智模型；**不是**法律意见。

## 为什么存在

生物医学项目同时引入：开放数据（MONDO）、署名/SA 数据（ChEMBL）、订阅源（UMLS、MedDRA）、AGPL 解析器、用途受限模型（BiomedCLIP）等。若义务只存在于 README 某段或口头约定，换人或换环境就会失控。

NOTICE + 可执行闸门把**数据许可**与**软件/模型许可**分开登记，并与 tier 过滤、组件 `assert_component_cleared` 对齐。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 数据 vs 软件 | 两面分开（`POLICIES` vs `COMPONENTS`） | 一张「依赖表」搅在一起 |
| 执行点 | 代码抛 `LicenseViolation` | 仅文档警告 |
| pending 组件 | 默认阻断（PoC 可 accept） | 无声带进生产 |
| 修改标注 | knowhere Apache §4(b) 进 NOTICE + README | 只保留版权头 |
| 本页角色 | 开发者索引 | 替代完整 NOTICE 正文 |

## 设计与实现

### 两面义务

| 面 | 管什么 | 执行点 |
|---|---|---|
| 数据 | Tier 0–3 源 | `licensing.POLICIES`、`named_graph_uri`、`LicenseScope` |
| 软件/模型 | 第三方组件 | `licensing.COMPONENTS`、`assert_component_cleared`、NOTICE 正文 |

数据面细节见 [Tier 策略](../licensing/tiers.md)；组件面见 [组件闸门](../licensing/components.md)。

### 已 cleared 示例

**Ontos-AI/knowhere**（Apache-2.0）：解析路径衍生自该项目，须保留许可声明并按 §4(b) **标注修改** —— README 与 NOTICE 均指向此处。`COMPONENTS['knowhere'].review = 'cleared'`。

### Pending（待法务核实）

| 组件 | 风险摘要 | 登记 ID |
|---|---|---|
| PyMuPDF / PyMuPDF4LLM | AGPL / 对外服务可能需商业许可 | `pymupdf4llm` |
| Docling | MIT；权重若另有条款须单独登记 | `docling` |
| MinerU | 附加商业门槛 + 在线服务标示义务 | `mineru` |
| BiomedCLIP | MIT 权重 + 模型卡「任何部署用途超出范围」 | `biomedclip` |

`review=pending` 时默认 `assert_component_cleared` 抛 `LicenseViolation`。本地 PoC：

```bash
HMD_ACCEPT_UNCLEARED_COMPONENTS=true   # 默认；启动 warnings 留痕
```

生产务必 `HMD_ACCEPT_UNCLEARED_COMPONENTS=false`。

### 数据源登记（非 COMPONENTS）

新增外部语料须同时：

1. `data/registry/sources.yaml` 声明 `license` / `tier`；
2. RDF 命名图 URI 含 tier（见 [rdf](../ontology/rdf.md)）；
3. NOTICE 增加出处段落（若许可要求）。

种子伪源 `SEED_INTERNAL` 不冒充外部权威。

### 与评测 / 服务的关系

- `hmd eval --entitlements MOCK_LICENSED`：评测 Bridge 许可还原，**不是**生产凭据模型。
- `hmd serve`：默认不信任 `X-HMD-Entitlements`；生产由网关注入（见 [serve](../tools/serve.md)）。

### 开发者检查清单

| 动作 | 必须同步 |
|---|---|
| 新增 pip / 模型依赖 | `COMPONENTS` + NOTICE + 调用点 `assert_component_cleared` |
| 新增语料源 | registry + tier + 命名图 + NOTICE（若需） |
| 对外 README 声称「已 clear」 | `review=cleared` 且 README 绊线测试通过 |
| 衍生 knowhere 代码 | NOTICE 修改说明 + §4(b) |

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| README 不得把 pending 写成 cleared | `test_readme` 等失败 |
| TIER_2+ 无凭据不可见 | 越权 P0 |
| BiomedCLIP 不得仅记 MIT | 低估部署限制 |
| NOTICE 与 COMPONENTS 一致 | 法务与工程两套真相 |

失败模式：

- **只改 requirements.txt**：闸门未登记，义务不可执行；
- **生产沿用 PoC accept_uncleared**：AGPL / 用途风险进生产；
- **把 MOCK_LICENSED 当生产模式**：订阅源可见性错误。

## 如何验证

```bash
uv run pytest tests/test_licensing.py tests/test_readme.py -q
uv run python -c "from biomed_ontology.licensing import uncleared_components; print([c.component_id for c in uncleared_components()])"
```

完整条文以根目录 `NOTICE` 为准；BIOS 子集另见 `data/foundation/NOTICE_BIOS.md`。
