# CLI 速查

入口：`uv run hmd`（`biomed_ontology.cli:app`）。  
构建入口：根目录 `Taskfile.yml`（`task`）。**实测数字以 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md) 为准**；本页说明命令职责与手册索引。

## 为什么存在

仓库能力横跨语料解析、KB 构建、Milvus 索引、双面评测、Foundation 三后端、Semantic Access 服务与演进挖掘。CLI 是**运维与研发的主操作面**，把 HTTP 入口收敛到唯一的 `hmd serve`，把评测拆成可记忆的一组子命令。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 对外 HTTP | 仅 `hmd serve`（REST + MCP） | 第二套 Foundation HTTP 进程 |
| 评测拆分 | `hmd eval` vs `hmd foundation golden-eval` | 单命令混跑 |
| 演示 | `hmd demo` 对齐 golden 场景（13 个） | 手写 curl 脚本 |
| 契约导出 | `hmd contract` | 手工复制 OpenAPI |
| 任务编排 | `task` 封装 docker / gen / check | 散落 shell 脚本 |
| 长任务 UX | Rich header/metrics + `tqdm.rich` 进度 | 裸 `print` 刷进度 |

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
| `hmd signals [--from-lake]` | 演进信号与 KGCL 挖掘；`--from-lake` 读 Iceberg obs_* | [evolution](../evolution/loop.md) |
| `hmd parse` | 单篇 PDF → 语料 YAML | [layout](../parse/layout.md) |
| `hmd sources` | 注册表与采购插槽 | [tiers](../licensing/tiers.md) |

### Foundation（世界模型）

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd foundation resolve [--json]` | 文本 → `HMD:ENT:*` + 反查别名（Rich）；无 ENT 时可附 BIOS surfaces | [IdentityService](../ontology/identity.md) |
| `hmd foundation lookup-bios [--query|--external-id|--bios-curie] [--json]` | 公开 BIOS 概念卡（不 mint ENT） | [Foundation](../architecture/foundation.md) |
| `hmd foundation golden [--compact] [--json]` | 金路径验收（Rich） | 同上 |
| `hmd foundation golden-eval [--compact] [--json]` | 多路径金标评测（Rich） | [golden-path](../ontology/golden-path.md) |
| `hmd foundation sync` | YAML → GraphDB Named Graphs + Milvus Evidence | 同上 |
| `hmd foundation bios-load` | BIOS 全量（默认）/ `--subset` | [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md) |
| `hmd foundation evolve-mine [--json] [--compact] [--include-lake/--no-include-lake]` | unmapped/低置信 → KGCL 候选（**不改**本体） | [evolution](../evolution/loop.md) |
| `hmd foundation evolve-enrich [--from …] [--llm/--no-llm]` | filter + 默认 LLM 裁决 + 提案；无 API key 自动跳过 LLM | 同上 |
| `hmd foundation evolve-review` / `approve` / `reject` / `apply` / `verify` | 提案队列 → 人工闸门 → Git 策展面 dry-run/write（L1/L2）→ 再 resolve | 同上 |
| `hmd foundation claim-review` / `claim-promote` | 列出 extracted；人审后只写 `ontology/claims/` | [extract](../ontology/extract.md) |
| `hmd foundation source-load --source hgnc` | 从 catalog/entities xref 装 HGNC；不改 `HMD:ENT:*`。`umls_subset` 无 ACK 拒、有 ACK 仍未实现 | [Foundation](../architecture/foundation.md) |
| `hmd foundation zingg-run [--mode …]` | 物化/导出模糊 matches | [evolution](../evolution/loop.md) |
| `hmd lake init` | 创建 Iceberg 表 | [pillars](../observability/pillars.md) |
| `hmd lake ingest-doc` | 单文档入湖（经 IngestQA） | [IngestQA](../parse/ingest-qa.md) |
| `hmd lake obs-replay` / `connect-status` / `maintain` | WAL 回放；Connect 状态；expire+optimize | [pillars](../observability/pillars.md) |
| `task obs:up` / `task obs:replay` / `task zingg:run` | Redpanda；WAL 回放；本地 stub Zingg | 同上 |
| `task ontology:validate` | Ontology-as-Code + Golden Path 校验 | [toolchain](../ontology/toolchain.md) |

### 生产平面（`hmd pipeline`）

无 Prefect Server 时也可 `flow()` 本地跑。平面隔离见 [Document Pipeline](../architecture/document-pipeline.md)。

| 命令 | 作用 |
|---|---|
| `literature-refresh` | 脏 PDF → IngestQA → 单篇 index；失败进 quarantine |
| `literature-reindex` | 文献面全量 recreate（低频；日常用 refresh） |
| `ingest` / `ingest-batch` | 单篇 / 清单入仓；IngestQA 不过不写 sink |
| `bios-bootstrap` | BIOS 冷启动；`--subset` 不拉全量 |
| `sync` / `catalog-publish` | 一次 replace 种子图；fingerprint 未变 no-op |
| `identity-match` | 生产禁 stub；`--dev` 仅 `HMD_ENV≠prod` |
| `data-loop-mine` / `enrich` / `apply` | enrich 停在提案；apply 只消费 `approved` |
| `eval --suite cheap\|release` | cheap = validate+identity+extraction |
| `replay` | 按 `doc_id` / `reason` 回放 quarantine |
| `ops-snapshot` / `slo-gate` | 新鲜度；红不回滚湖 |
| `claim-promote` | 只写 YAML，不 INSERT knowledge 边 |

HTTP / MCP **只走** `hmd serve --mcp`（含 `get_entity_context` 等 10 个 Foundation ops）。

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
| `task check` | lint（ruff + ty）+ 全量测试 |
| `task check:nlu` | 瘦安装剖面（`hmd-nlu`） |
| `task lint` | ruff check + ruff format --check + ty check |
| `task fmt` | ruff format + autofix |
| `task gen` | LinkML → `_generated/`（含 `hmd_enterprise`） |
| `task docs` / `docs:serve` | 手册严格构建 / 预览 |
| `task milvus:up` / `milvus:down` | Milvus 子集 compose |
| `task foundation:up` | GraphDB + OM + Milvus 全栈 + BIOS init + sync |
| `task foundation:smoke` | 健康检查 |
| `task foundation:init` | BIOS load + foundation sync |
| `task foundation:up:bern2` | 额外启动 BERN2 profile |
| `task evolve:e2e` | 合成 fixture：enrich→approve→apply(sandbox) |
| `task lake:expire` | Iceberg 快照保留（obs 90d / scorecard 180d） |

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
