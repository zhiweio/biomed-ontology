# 设计不变量

改代码前用这张清单自检。违反任一则：**宁可红灯或显式「未运行」，也不许绿报表撒谎**。

完整决策背景见 [附录 · D1–D12](appendix/decisions.md)。

---

## 1. 为什么存在

本仓库横跨检索评测、许可合规、双后端 World Model 与湖仓入湖。没有成文不变量时，「能跑」的 PoC 补丁会悄悄破坏：

- 评测可复现性（静默回落、fake 嵌入器冒充真模型）
- 许可隔离（先检出再裁剪泄漏存在性）
- 身份一致性（`HMD:ENT:*` vs 旧 `HMD:SUB` vs BIOS 主键）
- Foundation 三后端契约（YAML fallback）

不变量表是 PR 自检与 on-call 的**最低纪律**。

---

## 2. 设计取舍

| 原则 | 含义 |
|---|---|
| 失败要大声 | Milvus/精排/GraphDB 不可达 → 标未运行或硬失败 |
| 单一装配路径 | `open_dual_surface()` 服务 demo/eval/serve |
| 候选期许可 | `LicenseScope.permits` 在检索候选生成期 |
| 身份分层 | Enterprise ID 主键；外部 ID 仅 xref |
| 演进不自动 apply | `evolve-mine` 只产出 KGCL/JSON |

---

## 3. 设计与实现（按域）

### 3.1 检索与评测

| 不变量 | 实现锚点 |
|---|---|
| 无静默回落 | `runtime._require_milvus_literature_backend`；eval 臂状态机 |
| `fake` 需显式 | CLI `--allow-fake` |
| 集合盖戳 | Milvus `description` 含 `embedder=` |
| 融合不下推 | `search.rrf_fuse` 进程内；Milvus 只返各列名次 |
| 许可在候选期 | `LicenseScope` + `HybridSearcher._graph_allowed` |
| 图通道同滤 | `_graph_allowed` 与后端同一套 license/modality/figure_type/labels |
| dangling gold | `eval/retrieval.py` 整次评测拒绝出数 |
| 豁免须署名 | `waiver` + `waiver_owner` |
| 数字绊线 | README 实测表由 `tests/test_readme.py` 看守 |

### 3.2 本体与 ID

| 不变量 | 实现锚点 |
|---|---|
| Enterprise ID 主键 | `enterprise_id_for`、`HMD:ENT:*` |
| 目录 SSOT | `ontology/catalog/`；`catalog_files()` |
| 别名带 scope | `SynonymScopeEnum`；`normalize/matchers.py` |
| 不确定不猜 | D3：`alternatives` top-k |
| 链接双向建 | `GraphDbNeighborhood.adjacency_many` 合成反向 |
| 跨类型最多一跳 | `walk_neighbors` 的 `crossed` 状态 |
| 别名扩展 ≠ search-around | `Normalizer.expand` vs `neighborhood.neighbors` |
| Evolution 不自动改本体 | `foundation/evolve.py` |

### 3.3 Foundation / Evidence

| 不变量 | 实现锚点 |
|---|---|
| Milvus 必选 | 文献 + `foundation_evidence` |
| Semantic Ops 隐藏后端 | `FoundationApi`；无裸 SPARQL 工具 |
| Knowledge ≠ Truth | claim + PROV + Evidence |
| extracted ≠ validated | `sync._claims_turtle` 物化条件 |
| 同 doc_id 幂等 | `lake/` 先删后写 |
| extracted 图独立 | `GRAPH_PROVENANCE_EXTRACTED` |
| 双线并行 | Document Pipeline |
| BERN2 双写硬依赖 | `lake ingest-*` |
| BIOS 常挂 GraphDB | `graph/biomedical` |
| OM ≠ 第二图谱 | Glossary 约束 |
| BIOS 许可闸门 | `HMD_BIOS_LICENSE_ACK` |
| GraphDB Free ≠ 生产 | 运维文档纪律 |

### 3.4 解析与资产

| 不变量 | 实现锚点 |
|---|---|
| 路径唯一拼接点 | `resolve_asset` / `asset_dir_name` |
| 文件名不取自正文 | `safe_asset_name` |
| 读不到像素要可见 | 视觉列失败可观测 |
| `parsed/` 进库 | `pipeline.build_literature_base` glob |

### 3.5 Semantic Access 与观测

| 不变量 | 实现锚点 |
|---|---|
| `_invoke` 唯一入口 | `tools/api.py` |
| Provenance / trace_id 一等公民 | D6；`ObservabilityHub` |
| 还原共用 permits | Citationware 不旁路许可 |
| 组件 pending 要闸门 | `assert_component_cleared` |

---

## 4. 不变量与失败模式（违反示例）

| 违反 | 典型症状 | 谁最先发现 |
|---|---|---|
| 静默 Milvus 回落 | eval 绿但生产无向量检索 | `test_eval_*` / 运维 |
| 图通道不过滤 tier | 无权用户看到受限 chunk | 许可审计 |
| 合并 expand + search-around | 竞品药名污染 BM25/图通道 | Q4 类 gold 掉分 |
| sync CLEAR extracted | 湖侧 claim 消失 | 重跑 ingest 后 golden 失败 |
| 用 `data/seed/` 当 SSOT | 身份与 Foundation 分裂 | `test_seed_build` / ER eval |
| 手改 `_generated/` | gen 后神秘回归 | CI `task gen` diff |

---

## 5. 如何验证

### PR 自检（最短）

- [ ] 新臂/新后端失败时，报表如何显示？会不会假装跑过？
- [ ] 是否引入第二处拼资产路径 / 第二份 tier 判断 / 第二套别名表？
- [ ] gold 键是否对照 `dump_sections`？
- [ ] 若动了评测数字，README 与豁免正文是否同步？
- [ ] `task check` 与相关 pytest 是否绿？

```bash
task check
uv run pytest tests/test_readme.py tests/test_invariants.py -q 2>/dev/null || uv run pytest tests/ -q
uv run hmd eval --entitlements MOCK_LICENSED --compact
```

相关：[分层架构](architecture/layers.md)、[检索 hybrid](retrieval/hybrid.md)、[Foundation](architecture/foundation.md)。
