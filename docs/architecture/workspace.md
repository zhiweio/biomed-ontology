# uv workspace 与依赖剖面

源码：根目录 `pyproject.toml`（`[tool.uv.workspace]`）、`packages/hmd-*/pyproject.toml`。
代码仍在 `src/biomed_ontology/`；workspace 包声明**依赖剖面**，不做大爆炸 import 重命名。

---

## 1. 为什么存在

全栈默认安装拉 torch、docling、MinerU、Milvus 模型。身份归一化与关系抽取不需要这些重量依赖。
把仓库拆成七个 workspace 成员，让 CI 与下游可以 `uv sync --package hmd-nlu` 做瘦安装，同时保持单一 `biomed_ontology` 命名空间。

---

## 2. 设计取舍

| 决策 | 理由 | 放弃 |
|---|---|---|
| 代码仍在 `src/biomed_ontology/` | 避免一次改完所有 import | 立刻拆成七个可独立发布的源码树 |
| 七个剖面包 | 按能力切依赖，而不是按目录切仓库 | 多 git repo |
| 根仓仍是伞项目 | 现网 `uv sync` 一次拉全栈 | 强迫每人先选包 |
| extras 声明 layout / embed / vision / lake | 与剖面互补，便于按需加回重量依赖 | 把 torch 写进 `hmd-nlu` |

---

## 3. 设计与实现

### 3.1 成员

| 包 | 职责 | 典型依赖 |
|---|---|---|
| `hmd-contracts` | LinkML 生成物 / licensing / alias / GraphClient DTO | pydantic、linkml-runtime |
| `hmd-core` | Settings / observability / registry | pydantic-settings、structlog |
| `hmd-ingest` | parse / tree / lake steps / IngestQA | hmd-contracts、hmd-core |
| `hmd-nlu` | normalize / BERN2 / extract / IdentityService | hmd-contracts、hmd-core、httpx |
| `hmd-kg` | GraphDB / sync / world / biomedical sources | rdflib、httpx |
| `hmd-index` | embed / search / rerank / Evidence Index | hmd-contracts、hmd-core |
| `hmd-access` | tools / service / CLI / eval | 聚合上列 + typer / fastapi |

根 `pyproject.toml` 用 `[tool.uv.sources]` 把七个包标为 `{ workspace = true }`。

### 3.2 安装面

```bash
uv sync --extra docs --extra dev          # 全栈（默认）
uv sync --package hmd-nlu                 # 身份 / 抽取，不拉 torch / docling
task check:nlu                            # 瘦安装 CI
```

GitHub Actions 的 `nlu-slim` job 验证 `hmd-nlu` 可独立解析，且不把 MinerU / Docling 拉进该剖面。

### 3.3 与手册分层的关系

| 业界层 | 主要剖面 |
|---|---|
| Lakehouse / 入湖 | `hmd-ingest` |
| Scientific KG | `hmd-kg` |
| Evidence Index | `hmd-index` |
| Ontology Services | `hmd-nlu` + `hmd-contracts` |
| AI Context APIs | `hmd-access` |

---

## 4. 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| `hmd-nlu` 不声明 torch / docling / mineru | 瘦安装失去意义 |
| 业务 import 仍走 `biomed_ontology.*` | 双命名空间分裂 |
| 根仓 `uv sync` 仍能跑全栈 | 现网联调断裂 |
| workspace 成员必须能被 hatchling 发现 | `uv lock` / CI 失败 |

---

## 5. 如何验证

```bash
uv run pytest tests/test_workspace_packages.py -q
task check:nlu
```

相关：[分层与产品栈](layers.md)、[快速开始](../getting-started.md)。
