# 目录地图

本页描述仓库**物理布局**与**逻辑分层**的对应关系。设计层说明见 [分层与产品栈](../architecture/layers.md)、[Foundation](../architecture/foundation.md)。策展接线见 [策展资产与运行时机制](../ontology/curation-and-runtime.md)。

## 为什么存在

`biomed-ontology` 同时承载：LinkML 契约、Ontology-as-Code 策展、语料解析、混合检索、World Model 三后端、Semantic Access 服务与评测 gold。没有一张一致的目录地图，新人会在 `data/`、`ontology/`、`schema/`、`src/` 之间迷路，误把非 SSOT 目录当作权威源。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 契约 SSOT | `schema/` + `task gen` → `_generated/` + `schema/generated/` | 手写 Pydantic 与 LinkML 双份 |
| 实例策展 | `ontology/`（entities / dictionary / claims / catalog / mappings…） | 把 OWL 当运行时唯一真相 |
| 运行时包 | `src/biomed_ontology/` + `packages/hmd-*` workspace 剖面 | 多 repo 拆分 |
| 评测数据 | `data/gold/` 与代码同仓 | 外链不可复现数据集 |
| 任务入口 | `Taskfile.yml` | 散落 shell / 第二套任务文件 |

## 设计与实现

```text
biomed-ontology/
├── README.md                 # 命令 + 实测数字（测试绊线）
├── Taskfile.yml              # 统一任务入口
├── NOTICE                    # 出处与许可义务（法务面）
├── mkdocs.yml                # 本手册导航
├── packages/                 # uv workspace 依赖剖面（hmd-contracts … hmd-access）
├── docs/                     # 手册源码
├── schema/                   # LinkML SSOT
│   ├── hmd_*.yaml
│   ├── shapes/               # projection.shacl.ttl
│   └── generated/            # OWL / JSON Schema / SHACL 生成物
├── ontology/                 # Ontology-as-Code 策展面
│   ├── entities/             # 企业实体 HMD:ENT:*
│   ├── dictionary/           # ER Exact 词典
│   ├── claims/               # KnowledgeClaim
│   ├── mappings/             # BIOS / BERN2 / ChEBI / zingg
│   ├── catalog/              # 文献 ENT 目录 + ambiguity
│   ├── extract/              # MetricVocab（table_metrics.yaml）
│   └── examples/golden_path/
├── data/
│   ├── foundation/           # 运行投影样例（非身份 SSOT）
│   ├── corpus/ + parsed/     # 语料 YAML
│   ├── gold/                 # 评测 query、targets
│   ├── registry/             # 源与采购
│   └── cache/                # BIOS / 模型权重缓存
├── docker/
│   ├── milvus-standalone.yml
│   ├── docker-compose.foundation.yml
│   ├── bern2/
│   └── secrets/              # graphdb.license（gitignore）
├── scripts/
└── src/biomed_ontology/
    ├── identity.py           # IdentityService
    ├── pipeline.py           # KB 装配入口
    ├── runtime.py            # open_dual_surface
    ├── foundation/           # World Model + Context Pack + sync / resolve / bios
    ├── ingest/               # 种子 / catalog 构建
    ├── ontology/             # links / rdf / ids / neighborhood / metrics
    ├── normalize/ + alias/
    ├── parse/ + corpus/
    ├── search/ + embed/ + rerank/
    ├── tools/ + service/
    ├── lake/                 # ingest / IngestQA / Evidence Index / Iceberg
    ├── licensing.py
    ├── observability/ + quality/ + evolution/
    ├── eval/
    └── _generated/           # task gen 产物（勿手改）
```

### 逻辑分层 ↔ 目录

与 [分层与产品栈](../architecture/layers.md) 的 L0–L8 对齐：

| 层 | 主要目录 | 职责 |
|---|---|---|
| L0 Source | `data/registry/`、`registry/` | 源、tier、采购插槽 |
| L1 术语 | `ontology/catalog/`、`ingest/` | ENT 目录、链接 |
| L2 语义 | `schema/`、`_generated/` | LinkML、OWL/SHACL |
| L3 身份 | `identity.py`、`normalize/`、`foundation/resolve.py` | IdentityService |
| L4 语料 | `parse/`、`corpus/`、`lake/` | Router、IngestQA、TriModal |
| L5 检索 | `search/`、`embed/`、`rerank/` | HybridSearcher、Milvus |
| L6 访问 | `tools/`、`service/`、`foundation/api.py` | Semantic Access |
| L7 观测 | `observability/` | 四支柱 |
| L8 演进 | `evolution/`、`quality/` | 信号、KGCL、QualityGate |
| 横切 | `foundation/`、`eval/`、`data/gold/` | World Model、评测 |

### 关键入口文件

| 文件 | 作用 |
|---|---|
| `cli.py` / `cli_foundation.py` / `cli_lake.py` | `hmd` 子命令 |
| `identity.py` | `IdentityService` |
| `runtime.py` | `open_dual_surface`、Milvus 文献后端硬要求 |
| `service/deps.py` | `build_state` 单例 |
| `tools/api.py` | `TOOL_SPECS`、`ToolApi` |
| `foundation/api.py` | `SEMANTIC_OPS`、`FoundationApi` |
| `foundation/context_pack.py` | Context Pack |
| `lake/ingest_qa.py` | IngestQA |
| `foundation/sync.py` | `sync_world_model` |
| `foundation/resolve.py` | `EntityResolver` |
| `eval/suite.py` | `run_dual_eval` |

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 改 LinkML 后 `task gen` | `_generated` / `schema/generated` 与契约漂移 |
| 不手改 `_generated/` | 下次 gen 覆盖或 CI 失败 |
| 企业身份在 `ontology/`（catalog + entities） | 改错面、sync 读不到 |
| gold 键对齐 `parsed/` section | eval dangling |
| NOTICE 与 `COMPONENTS` 同步 | 义务不可执行 |
| secrets 不入 git | `docker/secrets/` gitignore |

失败模式：

- **在 `ontology/owl` 改 SSOT 却不走 schema + gen**：运行时契约不变；
- **只改 `ontology/entities` 不 `foundation sync`**：Semantic Ops 仍读旧图；
- **只改 corpus 不重建 index**：Milvus 与 gold 不一致；
- **把 `examples/golden_path` 或 `data/foundation` 当生产身份 SSOT**：仅为样例 / 投影。

## 如何验证

```bash
task gen && task check
task docs
uv run python scripts/dump_sections.py | head
```

CLI 入口见 [cli](cli.md)；许可见 [notice](notice.md)。
