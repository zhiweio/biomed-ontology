# 快速开始

本页是检查清单与「第一天会踩的坑」。完整命令与实测数字见仓库 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。构建入口是 **Taskfile**（`task …`）。

## 你在搭什么

不是 chatbot，也不是「加了别名的检索引擎」。你在搭：

1. **Enterprise World Model**：`HMD:ENT:*` + GraphDB / Evidence Index / OpenMetadata  
2. **Ontology Semantic Layer**：术语与身份、层级、类型化关系、事实、证据、Citationware、许可与演进  
3. **Semantic Access**：单一 `hmd serve`（MCP/REST）把上述能力交给仓外 Agent  

跑通下面闭环，再读机制章。

## 最小闭环（语义层 + 检索）

```bash
uv sync --extra dev --extra rdf --extra ontology --extra parse --extra vector --extra service

uv run hmd kb        # 构建知识库：看 stats + warnings
uv run hmd demo              # 8 个演示场景（Rich + 可证伪断言）
uv run hmd demo --compact    # 仅 Trace 摘要
uv run hmd demo --id D7      # 单场景
task milvus:up
uv run hmd index --recreate                    # 默认 multimodal-bio
uv run hmd eval --entitlements MOCK_LICENSED   # Rich：归一化+检索+targets
uv run hmd eval --entitlements MOCK_LICENSED --compact  # 仅 Trace
uv run hmd serve --port 8000
task check           # ruff + 全量测试
```

`hmd kb` 仍构建过渡文献 KB（corpus + 本地检索）。身份权威已迁到
[`ontology/`](../ontology/README.md)（entities / dictionary / claims）+ ER，
[`data/seed/`](../data/seed/DEPRECATED.md) 已退役，勿再当 CURIE SSOT。

`hmd demo` / `eval` / `serve` 经 `open_dual_surface()`：文献 ToolApi + Foundation WM。

## Foundation 世界模型闭环

手册：[Foundation 架构](architecture/foundation.md)。

```bash
# 需 docker/secrets/graphdb.license；BIOS 全量需 ACK
export HMD_BIOS_LICENSE_ACK=poc
# CI / 无许可证：export HMD_BIOS_INIT=subset

task foundation:up
uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation golden --candidate HMPL-504   # WM + 文献 search/restore
uv run hmd foundation evolve-mine
uv run hmd serve --mcp
```

金路径：`DrugCandidate → Target → Disease → Evidence → ELN Asset`（+ 文献腿）。

## Milvus（Evidence Index，必选）

```bash
task milvus:up
uv run hmd index --recreate
uv run hmd eval --entitlements MOCK_LICENSED
```

- 默认 embedder = **multimodal-bio**（五列最全），无需再选  
- 权重解析：本地 → `HMD_MODEL_HUB` → Gitee 兜底。见 [嵌入器](retrieval/embedders.md)

## 验收时你会碰到的纪律

| 现象 | 原因 | 不要做的事 |
|---|---|---|
| Milvus 臂「未运行」 | 容器没起或集合不存在 | 期待静默回落到本地 |
| `fake` 被拒绝 | 报告口径必须用真模型 | 验证接线时请显式 `--allow-fake` |
| 建表「最多 4 向量列」 | Milvus 默认上限 | 配 `PROXY_MAXVECTORFIELDNUM`（见 docker compose） |
| `LicenseViolation` 组件 | pending 且显式 `accept=false` | PoC 默认已放行；生产保持 `HMD_ACCEPT_UNCLEARED_COMPONENTS=false` |
| BIOS 全量被拒 | 未设 `HMD_BIOS_LICENSE_ACK` | 跳过闸门硬灌；应 ACK 或用 `HMD_BIOS_INIT=subset` |
| GraphDB 起不来 / `/rest/repositories` 404 | `docker/secrets/graphdb.license` 被 Docker 建成空目录，或 SE/EE license 不可读 | GraphDB 10 Free **不需要** license；删掉空目录后重启。SE/EE 才挂真实 license 文件 |
| eval 直接拒绝出数 | gold 键 dangling | 用 `scripts/dump_sections.py` 对照 |

!!! warning "待法务核实"
    PyMuPDF、MinerU、BiomedCLIP：`review=pending`。BIOS_v3 为 CC-BY-NC-ND 4.0。
    详见 [组件闸门](licensing/components.md) 与
    [NOTICE_BIOS](https://github.com/zhiweio/biomed-ontology/blob/main/data/foundation/NOTICE_BIOS.md)。

## 建议阅读顺序（第一周）

1. [Foundation 世界模型](architecture/foundation.md) + [分层 L0–L8](architecture/layers.md)  
2. [links / search-around](ontology/links.md) + [hybrid RRF](retrieval/hybrid.md)  
3. [ARMS](eval/arms.md) + [不变量](invariants.md)  
4. 按任务选：Semantic tools / Evidence Index / 许可  

手册预览：`uv sync --extra docs && task docs:serve`。
