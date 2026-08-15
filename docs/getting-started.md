# 快速开始

本页是**第一天操作清单**与常见踩坑索引。完整命令说明、环境变量与实测数字见仓库
[README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
构建入口统一为 **Taskfile**（`task …`）。

---

## 1. 为什么存在

本仓库不是 chatbot，也不是「加了别名的检索引擎」。你在搭的是三层能力：

| 层 | 一句话 | 主键 / 锚点 |
|---|---|---|
| **Enterprise World Model** | 企业研发世界的可查询语义图 | `HMD:ENT:*` |
| **Ontology Semantic Layer** | 术语、层级、类型化关系、事实、证据、许可与演进 | 目录 `BuiltConcept` + GraphDB 边 |
| **Semantic Access** | 单一 `hmd serve`（MCP/REST）把上述能力交给仓外 Agent | `ToolApi` + `FoundationApi` |

跑通下面两条闭环，再深入机制章。机制细节见 [分层与产品栈](architecture/layers.md)、[Foundation](architecture/foundation.md)。

---

## 2. 设计取舍

| 取舍 | 选择 | 代价 |
|---|---|---|
| 运行时入口 | `runtime.open_dual_surface()` 统一装配 | 离线 `hmd kb` 与在线 `serve` 必须走同一套文献构建逻辑 |
| 身份权威 | `IdentityService` + `ontology/catalog/` + `HMD:ENT:*` | 公开 ID 只做 xref；不从文档 mint 企业主键 |
| 文献检索后端 | Milvus 必选，禁止内存词法回落 | 本地必须先 `task milvus:up` + `hmd index` |
| 图通道边权威 | GraphDB + `ensure_catalog_graphs` | 含 GRAPH 臂的评测/服务需要 GraphDB 可达 |
| 评测口径 | 失败标「未运行」，不静默冒充成功臂 | Milvus/精排不可达时整臂拒绝出数 |

---

## 3. 设计与实现

### 3.1 最小闭环（语义层 + 检索）

```bash
uv sync --extra docs --extra dev
# 瘦安装（身份 / 抽取，不拉 torch / docling）：
# uv sync --package hmd-nlu

uv run hmd kb        # 构建文献 KB：stats + warnings
uv run hmd demo              # 13 个演示场景（D1–D8 文献面 + W1–W3 / B1–B2 世界模型面）
uv run hmd demo --compact    # 仅 Trace 摘要
uv run hmd demo --id D7      # 单场景
task milvus:up
uv run hmd index --recreate                    # 默认 multimodal-bio
uv run hmd eval --entitlements MOCK_LICENSED   # Rich：归一化+检索+targets
uv run hmd eval --entitlements MOCK_LICENSED --compact
uv run hmd serve --port 8000
task check           # ruff + ty + 全量测试
```

**调用链（文献面）：**

```text
hmd kb / demo / eval / serve
    → runtime.open_dual_surface()
        → IdentityService.from_world()
        → pipeline.build_literature_base()     # ENT 目录 + corpus
        → search.HybridSearcher                # Milvus + GraphDbNeighborhood
        → tools.ToolApi.from_backends()
        → foundation.FoundationApi             # World Model Semantic Ops
```

- `build_literature_base`：只读 `ontology/catalog/*.yaml`（缺失硬失败），默认 `id_mode=enterprise`，身份为确定性 `HMD:ENT:*`。
- `hmd demo` / `eval` / `serve` 经 `open_dual_surface()` 共用同一装配路径。
- KB 图投影（`GraphStore`）后端为 GraphDB；`with_graph=True` 或含 GRAPH 通道时需 `task foundation:up`。默认构建 `with_graph=False`。

### 3.2 Foundation 世界模型闭环

```bash
export HMD_BIOS_LICENSE_ACK=poc   # 全量 BIOS；CI 用 export HMD_BIOS_INIT=subset
# 可选：观测入湖 + Zingg（默认连 Redpanda 127.0.0.1:19092；见 .env.example）
# task obs:up

task foundation:up
uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation golden --candidate HMPL-504
uv run hmd foundation evolve-mine
uv run hmd serve --mcp
```

**调用链（Foundation 面）：**

```text
hmd foundation *
    → foundation.world.load_world_model()
    → IdentityService.from_world()          # 同一目录 + EntityResolver
    → foundation.api.FoundationApi
    → foundation.sync.sync_world_model()   # YAML → GraphDB + Milvus + OM
```

金路径：`DrugCandidate → Target → Disease → Evidence → ELN Asset`（+ 文献检索腿）。详见 [Golden Path](ontology/golden-path.md)。

### 3.3 关键目录

| 路径 | 职责 |
|---|---|
| `ontology/catalog/` | 文献/检索 ENT 目录 SSOT（`substances.yaml` 等） |
| `ontology/entities/` | 金路径企业实体策展 |
| `ontology/dictionary/` | ER 企业词典 |
| `data/corpus/` | 语料 YAML（含 `parsed/` 子目录） |
| `schema/*.yaml` | LinkML SSOT → `task gen` |
| `packages/` | uv workspace 依赖剖面 |

---

## 4. 不变量与失败模式

| 现象 | 根因 | 正确做法 |
|---|---|---|
| Milvus 臂「未运行」 | 容器未起或集合不存在 | `task milvus:up` + `hmd index --recreate`；**不要**期待静默回落 |
| `fake` 被拒绝 | 报告口径要求真模型 | 接线验证时显式 `--allow-fake` |
| 建表「最多 4 向量列」 | Milvus 默认上限 | 配 `PROXY_MAXVECTORFIELDNUM`（见 docker compose） |
| `LicenseViolation` | pending 组件且 `accept=false` | PoC 默认放行；生产设 `HMD_ACCEPT_UNCLEARED_COMPONENTS=false` |
| BIOS 全量被拒 | 未设 `HMD_BIOS_LICENSE_ACK` | ACK 或 `HMD_BIOS_INIT=subset` |
| GraphDB `/rest/repositories` 404 | `docker/secrets/graphdb.license` 被建成空目录 | Free 版删空目录重启；SE/EE 挂真实 license |
| eval 拒绝出数 | gold 键 dangling | 用 `scripts/dump_sections.py` 对照 |
| GRAPH 通道无结果 | GraphDB 未灌目录图 | `ensure_catalog_graphs` 失败会硬报错 |
| IngestQA 阻断 | 空树 / 降级超阈 / 未登记来源 | 修解析或 registry，禁止静默入库 |

!!! warning "待法务核实"
    PyMuPDF、MinerU、BiomedCLIP：`review=pending`。BIOS_v3 为 CC-BY-NC-ND 4.0。
    详见 [组件闸门](licensing/components.md) 与 [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md)。

---

## 5. 如何验证

```bash
# 文献闭环
uv run hmd kb
uv run pytest tests/test_seed_build.py tests/test_eval_demo.py -q

# Foundation 闭环
task foundation:smoke
uv run hmd foundation golden --candidate HMPL-504 --json

# 全量守门
task check
```

**建议阅读顺序（第一周）：**

1. [Foundation 世界模型](architecture/foundation.md) + [分层与产品栈](architecture/layers.md)
2. [IdentityService](ontology/identity.md) + [links / search-around](ontology/links.md) + [hybrid RRF](retrieval/hybrid.md)
3. [ARMS](eval/arms.md) + [不变量](invariants.md)
4. 按任务选：Semantic tools / Evidence Index / 许可

手册预览：`uv sync --extra docs && task docs:serve`。
