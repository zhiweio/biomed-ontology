# CLI 速查

入口：`uv run hmd`（`biomed_ontology.cli:app`）。  
构建入口：根目录 `Taskfile.yml`（`task`）。**实测数字以 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md) 为准**；本页说明命令职责与手册索引。

## 为什么存在

仓库能力横跨语料解析、KB 构建、Milvus 索引、双面评测、Foundation 三后端、Semantic Access 服务与演进挖掘。CLI 是**运维与研发的主操作面**，把易混入口（如 `hmd serve` vs 已废弃的独立 foundation HTTP）收敛到可记忆的一组子命令。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 对外 HTTP | 仅 `hmd serve`（REST + MCP） | `foundation serve` |
| 评测拆分 | `hmd eval` vs `hmd foundation golden-eval` | 单命令混跑 |
| 演示 | `hmd demo` 对齐 golden 场景 | 手写 curl 脚本 |
| 契约导出 | `hmd contract` | 手工复制 OpenAPI |
| 任务编排 | `task` 封装 docker / gen / check | 散落 shell 脚本 |
| 长任务 UX | Rich header/metrics + `tqdm.rich` 进度（`index` / `bios-load` / lake ingest 等） | 裸 `print` 刷进度 |

长任务在 TTY 下显示进度条；管道 / CI / `TQDM_DISABLE=1` / `HMD_NO_PROGRESS=1` 时静默。`lake ingest-*` 的人读摘要走 stderr，stdout 仍为 JSON。

## 设计与实现

### 检索与语义层

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd kb` | 构建知识库并打印统计 / warnings | [pipeline](../architecture/pipeline.md) |
| `hmd demo [--id …] [--compact] [--json]` | 语义层能力验收（Rich，对齐 golden） | [tools](../tools/tools.md) |
| `hmd eval [--suite …] [--no-retrieval] [--compact] [--json]` | 双面 Scorecard：Identity + Literature + Bridge | [dual-surface](../eval/dual-surface.md) |
| `hmd foundation golden-eval [--compact] [--json]` | WM 三后端金路径（**不**并入 eval） | [golden-path](../ontology/golden-path.md) |
| `hmd index` | 写入 Milvus（默认 multimodal-bio；盖 embedder 戳） | [milvus](../retrieval/milvus.md) |
| `hmd serve [--mcp]` | 唯一 HTTP 入口：REST + MCP（默认 :8000） | [serve](../tools/serve.md) |
| `hmd contract` | 导出 OpenAPI / MCP 描述符 | [linkml](../architecture/linkml.md) |
| `hmd signals` | 演进信号与 KGCL 挖掘 | [evolution](../evolution/loop.md) |
| `hmd parse` | 单篇 PDF → 语料 YAML | [layout](../parse/layout.md) |
| `hmd sources` | 注册表与采购插槽 | [tiers](../licensing/tiers.md) |

### Foundation（世界模型）

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd foundation resolve [--json]` | 文本 → `HMD:ENT:*` + 反查别名（Rich） | [foundation](../architecture/foundation.md) |
| `hmd foundation golden [--compact] [--json]` | 金路径验收（Rich） | 同上 |
| `hmd foundation golden-eval [--compact] [--json]` | 多路径金标评测（Rich） | [golden-path](../ontology/golden-path.md) |
| `hmd foundation sync` | YAML → GraphDB Named Graphs + Milvus Evidence | 同上 |
| `hmd foundation bios-load` | BIOS 全量（默认）/ `--subset` | [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md) |
| `hmd foundation evolve-mine [--json] [--compact]` | unmapped/低置信 → KGCL 候选（**不改**本体） | [evolution](../evolution/loop.md) |
| `hmd foundation zingg-run` | 校验 Zingg matches 桩 | [foundation](../architecture/foundation.md) |
| `task ontology:validate` | Ontology-as-Code + Golden Path 校验 | [toolchain](../ontology/toolchain.md) |

HTTP / MCP **不走** `foundation serve`：统一用 `hmd serve --mcp`（含 `get_entity_context` 等 9 个 Foundation ops）。

### `hmd eval` 常用旗标

| 旗标 | 含义 |
|---|---|
| `--entitlements MOCK_LICENSED` | Bridge 许可用例与 tier 可见性 |
| `--suite identity,literature,bridge` | 子集套件；另有 EXTRA：`extraction`、`public_bios` |
| `--no-retrieval` | 跳过 Literature ARMS（仍可按配置跑 Identity/Bridge） |
| `--json` | 机器可读 `DualEvalReport` |

### `hmd index` 注意

| 场景 | 命令 |
|---|---|
| 生产 / 报告 | `hmd index --recreate`（Milvus 真后端） |
| 仅验接线 | `hmd index --embedder fake --allow-fake --recreate`（**不可入报告**） |

### Task 目标

| 目标 | 作用 |
|---|---|
| `task check` | ruff + 全量测试 |
| `task gen` | LinkML → `_generated/`（含 `hmd_enterprise`） |
| `task docs` / `docs:serve` | 手册严格构建 / 预览 |
| `task milvus:up` / `milvus:down` | Milvus 子集 compose |
| `task foundation:up` | GraphDB + OM + Milvus 全栈 + BIOS init + sync |
| `task foundation:smoke` | 健康检查 |
| `task foundation:init` | BIOS load + foundation sync |
| `task foundation:up:bern2` | 额外启动 BERN2 profile |

手册预览：`task docs:serve` → http://127.0.0.1:8000（MkDocs，与服务端口冲突时注意只开一个）。

### 常用组合

```bash
task milvus:up
hmd index --recreate
hmd eval --entitlements MOCK_LICENSED
hmd serve --mcp

task foundation:up
hmd foundation golden-eval
```

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 文献 serve 前 Milvus 已索引 | `open_dual_surface` 失败 |
| eval 与 golden-eval 分工 | 结论混淆（栈通 vs 本体增益） |
| fake embedder 不入报告 | 采购决策被 fake 数误导 |
| `MOCK_LICENSED` 仅评测 | 生产凭据由网关注入 |

失败模式：

- **未 `task milvus:up` 就 eval**：多臂 unavailable；
- **把 demo 当 eval**：demo 是能力验收，不是 targets 门禁；
- **端口 8000 冲突**：MkDocs 预览与 `hmd serve` 二选一或改端口。

## 如何验证

```bash
uv run hmd --help
task check
task docs          # 严格构建本手册
uv run pytest tests/test_readme.py -q   # CLI / README 绊线
```

目录结构见 [tree](tree.md)。
