# 设计不变量

改代码前用这张清单自检。违反任一则：宁可红灯或显式「未运行」，也不许绿报表撒谎。

## 检索与评测

| 不变量 | 说明 |
|---|---|
| 无静默回落 | Milvus / 精排不可达 → 标未运行；禁止 Local / NullReranker 顶替后仍写原臂名 |
| `fake` 需显式 | `--allow-fake`；假嵌入器产出不得当模型结论 |
| 集合盖戳 | `embedder=` 写在 description；索引/评测不一致直接退出 |
| 融合不下推 | Milvus 只返各列名次材料；RRF 在进程内，保住 `explain` |
| 许可在候选期 | 禁止「先检出再裁剪」泄漏存在性 |
| 图通道同滤 | `_graph_allowed` 与后端同一套 license/modality/figure_type/labels |
| dangling gold | 错误 `doc_id#section` → 整次评测拒绝出数 |
| 豁免须署名 | `waiver` + `waiver_owner`；达成后不得留 stale waiver |
| 数字绊线 | README 实测表由 `tests/test_readme.py` 看守 |

## 本体与 ID

| 不变量 | 说明 |
|---|---|
| Enterprise ID 主键 | 世界模型对外锚点是 `HMD:ENT:*`；BIOS/ChEBI 等只做 External Concept |
| 内部 CURIE 主键 | 检索底座：外部 ID 只做 xref（D1/D9） |
| 别名带 scope | BROAD 不进精确归一（D2） |
| 不确定不猜 | 消歧返回 top-k（D3）；Foundation unmapped → evolve-mine 候选 |
| 链接双向建 | 种子写正向；`LinkIndex` 建反向 |
| 跨类型最多一跳 | 防竞品关系污染召回 |
| 别名扩展 ≠ search-around | `_children` 与 `LinkIndex` 不合并 |
| Evolution 不自动改本体 | `evolve-mine` 只落 KGCL/JSON；禁止静默 apply |

## Foundation / Evidence

| 不变量 | 说明 |
|---|---|
| Milvus 必选 | Evidence Index；生产路径失败不回落 LocalBackend |
| Semantic Ops 隐藏后端 | Agent 默认不拼 SPARQL / 原始向量 API |
| Knowledge ≠ Truth | Claim + Provenance + Evidence 才可消费 |
| BIOS 许可闸门 | 全量需 `HMD_BIOS_LICENSE_ACK`；子集仅测试 / CI |
| GraphDB Free ≠ 生产 | 文档与运维不得把 Free 写成生产架构决策 |

## 解析与资产

| 不变量 | 说明 |
|---|---|
| 路径唯一拼接点 | 只许 `resolve_asset` / `asset_dir_name`；`DOC:` → `DOC_` |
| 文件名不取自正文 | 防路径穿越；`safe_asset_name` |
| 读不到像素要可见 | 禁止静默改编码 caption 还当视觉列成功 |
| `parsed/` 进库 | corpus 装配必须包含 `corpus/parsed/` |

## Semantic Access 与观测

| 不变量 | 说明 |
|---|---|
| `_invoke` 唯一入口 | 契约 → trace → 执行 → license → 落盘 |
| Provenance / trace_id 一等公民 | D6 |
| 还原共用 permits | 禁止还原旁路 |
| 组件 pending 要闸门 | `assert_component_cleared`；PoC 默认 accept，启动告警留痕；生产显式关闭 |

## PR 自检（最短）

- [ ] 新臂/新后端失败时，报表如何显示？会不会假装跑过？  
- [ ] 是否引入第二处拼资产路径 / 第二份 tier 判断 / 第二套别名表？  
- [ ] gold 键是否对照 `dump_sections`？  
- [ ] 若动了评测数字，README 与豁免正文是否同步？（测试会问）  
- [ ] `task docs` 与相关 pytest 是否绿？  

更多决策背景见 [附录 · D1–D12](appendix/decisions.md)。
