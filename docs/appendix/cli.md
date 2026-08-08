# CLI 速查

入口：`uv run hmd`（`biomed_ontology.cli:app`）。细节与实测数字以 README 为准。构建入口：`task`（根目录 `Taskfile.yml`）。

## 检索底座

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd kb` | 构建知识库并打印统计 / warnings | [pipeline](../architecture/pipeline.md) |
| `hmd demo [--id …] [--compact] [--json]` | 语义层能力验收（Rich，对齐 golden） | [tools](../tools/tools.md) |
| `hmd eval` | 归一化 + 检索消融 + targets（默认 multimodal-bio + 精排） | [ARMS](../eval/arms.md) |
| `hmd index` | 写入 Milvus（默认 multimodal-bio；盖 embedder 戳） | [milvus](../retrieval/milvus.md) |
| `hmd serve [--mcp]` | 唯一 HTTP 入口：REST + MCP（默认 :8000） | [serve](../tools/serve.md) |
| `hmd contract` | 导出 OpenAPI / MCP 描述符 | [linkml](../architecture/linkml.md) |
| `hmd signals` | 演进信号与 KGCL | [evolution](../evolution/loop.md) |
| `hmd parse` | 单篇 PDF → 语料 YAML | [layout](../parse/layout.md) |
| `hmd sources` | 注册表与采购插槽 | [tiers](../licensing/tiers.md) |

## Foundation（世界模型）

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd foundation resolve` | 文本 → `HMD:ENT:*` | [foundation](../architecture/foundation.md) |
| `hmd foundation golden` | 金路径验收 | 同上 |
| `hmd foundation golden-eval` | 多路径金标评测 | [golden-path](../ontology/golden-path.md) |
| `hmd foundation sync` | YAML → GraphDB Named Graphs + Milvus Evidence | 同上 |
| `hmd foundation bios-load` | BIOS 全量（默认）/ `--subset` | [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md) |
| `hmd foundation evolve-mine` | unmapped → KGCL 候选（不改本体） | [evolution](../evolution/loop.md) |
| `hmd foundation zingg-run` | 校验 Zingg matches 桩 | [foundation](../architecture/foundation.md) |
| `task ontology:validate` | Ontology-as-Code + Golden Path 校验 | [toolchain](../ontology/toolchain.md) |

HTTP / MCP 不走 `foundation serve`：统一用 `hmd serve --mcp`（含 `get_entity_context` 等 Semantic Ops）。

## 常用组合

```bash
hmd eval --entitlements MOCK_LICENSED
hmd index --recreate

# 仅验证接线（不可入报告）
hmd index --embedder fake --allow-fake --recreate

hmd serve --mcp
```

## Task 目标

| 目标 | 作用 |
|---|---|
| `task check` | ruff + 全量测试 |
| `task gen` | LinkML → `_generated/`（含 `hmd_enterprise`） |
| `task docs` / `docs:serve` | 手册严格构建 / 预览 |
| `task milvus:up` / `milvus:down` | 同项目 `hmd-foundation` 的 Milvus 子集 |
| `task foundation:up` | GraphDB + OM + Milvus 全栈 + BIOS init + sync |
| `task foundation:smoke` | 健康检查 |
| `task foundation:init` | BIOS load + foundation sync |
| `task foundation:up:bern2` | 额外启动 BERN2 profile |

手册预览：`task docs:serve` → http://127.0.0.1:8000
