# 目录地图

本页描述仓库**物理布局**与**逻辑分层**的对应关系，便于接手时快速定位代码与数据。设计层说明见 [分层架构](../architecture/layers.md)、[Foundation](../architecture/foundation.md)。

## 为什么存在

`biomed-ontology` 同时承载：LinkML 契约、Ontology-as-Code 策展、语料解析、混合检索、World Model 三后端、Semantic Access 服务与评测 gold。没有一张一致的目录地图，新人会在 `data/`、`ontology/`、`schema/`、`src/` 之间迷路，误把非 SSOT 目录当作权威源。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 契约 SSOT | `schema/` + `task gen` → `_generated/` | 手写 Pydantic 与 LinkML 双份 |
| 策展面 | `ontology/`（mappings、OWL 入口） | 把 OWL 当运行时唯一真相 |
| 运行时包 | `src/biomed_ontology/` 单包 | 多 repo 拆分（当前单体） |
| 评测数据 | `data/gold/` 与代码同仓 | 外链不可复现数据集 |
| 任务入口 | `Taskfile.yml` | Makefile |

## 设计与实现

```text
biomed-ontology/
├── README.md                 # 命令 + 实测数字（测试绊线）
├── Taskfile.yml              # 统一任务入口
├── NOTICE                    # 出处与许可义务（法务面）
├── mkdocs.yml                # 本手册导航
├── docs/                     # 手册源码（设计文档）
├── schema/                   # LinkML SSOT（含 hmd_enterprise）
├── ontology/                 # Ontology-as-Code 策展面（非运行时 SSOT）
│   ├── mappings/             # BIOS / BERN2 / ChEBI 等映射
│   ├── owl/ + shapes/        # Protégé / SHACL 入口说明
│   └── examples/golden_path/ # HMPL-504 金路径样例
├── data/
│   ├── seed/                 # 概念与歧义登记
│   ├── foundation/           # 企业实体 / 词典 / claims / evidence / BIOS
│   ├── corpus/ + parsed/     # 语料 YAML（解析产物）
│   ├── gold/                 # 评测 query、targets、bridge/resolve 金标
│   ├── registry/             # 源与采购（tier / license）
│   ├── assets/               # 渲染图块等静态资源
│   └── cache/                # BIOS / 模型权重缓存
├── docker/
│   ├── milvus-standalone.yml
│   ├── docker-compose.foundation.yml
│   ├── bern2/
│   └── secrets/              # graphdb.license（gitignore）
├── scripts/                  # dump_sections 等维护脚本
└── src/biomed_ontology/
    ├── pipeline.py           # KB 装配入口
    ├── runtime.py            # open_dual_surface（ToolApi + FoundationApi）
    ├── foundation/           # World Model + FoundationApi + sync
    ├── ingest/               # 种子构建
    ├── ontology/             # links / rdf / ids / neighborhood
    ├── normalize/ + alias/
    ├── parse/ + corpus/
    ├── search/ + embed/ + rerank/
    ├── tools/ + service/     # ToolApi、hmd serve、MCP
    ├── licensing.py          # tier + COMPONENTS 闸门
    ├── observability/ + quality/ + evolution/
    ├── eval/                 # dual eval、ARMS、targets、stats
    └── _generated/           # task gen 产物（勿手改）
```

### 逻辑分层 ↔ 目录（简表）

| 层 | 主要目录 | 职责 |
|---|---|---|
| L0 契约 | `schema/`、`_generated/` | LinkML、OpenAPI/MCP 生成源 |
| L1 数据 | `data/seed`、`data/corpus`、`data/registry` | 种子、语料、源 tier |
| L2 解析 | `parse/`、`corpus/` | PDF → 带 provenance 的 YAML |
| L3 本体 | `ontology/`、`ingest/`、`ontology/rdf` | 概念、链接、命名图 |
| L4 检索 | `search/`、`embed/`、`rerank/` | HybridSearcher、Milvus |
| L5 工具 | `tools/`、`service/` | Semantic Access、REST/MCP |
| L6 Foundation | `foundation/` | ER、GraphDB、Evidence、OM |
| L7 观测 | `observability/` | 四支柱 |
| L8 演进 | `evolution/`、`quality/` | 信号、KGCL |
| L9 评测 | `eval/`、`data/gold/` | dual-surface、ARMS、targets |

### 关键入口文件

| 文件 | 作用 |
|---|---|
| `cli.py` | 所有 `hmd` 子命令 |
| `runtime.py` | `open_dual_surface`、Milvus 文献后端硬要求 |
| `service/deps.py` | `build_state` 单例 |
| `tools/api.py` | `TOOL_SPECS`、`ToolApi` |
| `foundation/api.py` | `SEMANTIC_OPS`、`FoundationApi` |
| `eval/suite.py` | `run_dual_eval` |

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 改 LinkML 后 `task gen` | `_generated` 与契约漂移 |
| 不手改 `_generated/` | 下次 gen 覆盖或 CI 失败 |
| gold 键对齐 `parsed/` section | eval dangling |
| NOTICE 与 `COMPONENTS` 同步 | 义务不可执行 |
| secrets 不入 git | `docker/secrets/` gitignore |

失败模式：

- **在 `ontology/owl` 改 SSOT 却不走 ingest**：运行时 KB 不变；
- **只改 corpus 不重建 index**：Milvus 与 gold 不一致；
- **把 `examples/golden_path` 当生产数据**：仅为验收样例。

## 如何验证

```bash
task gen && task check
task docs
uv run python scripts/dump_sections.py | head
```

CLI 入口见 [cli](cli.md)；许可见 [notice](notice.md)。
