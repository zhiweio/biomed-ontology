# biomed-ontology

**AI-Ready Scientific Data Foundation for Drug Discovery**  
面向创新药研发的 **AI 原生科研数据基座**。

用企业内部实体 ID（`HMD:ENT:*`）锚定候选药、靶点、项目，挂上关系、可引用证据与
ELN/LIMS 资产，经 `hmd serve`（MCP/REST）把 **Data-for-Agent 契约**交给仓外 Agent。
公共生物医学知识（BIOS / 未来 UMLS 子集）只进 `graph/biomedical` 与 xref，
**不是企业主键**。图谱是六层栈里的一层，不是产品本身。

> BIOS provides the biomedical world. Enterprise Ontology provides the company's world.

**不做** Agent 编排、靶点发现应用、GNN / 组学平台，也不把裸 SPARQL / 湖 SQL 当 Agent 主契约。
交付的是可治理的语义世界与访问面。

### 仓外 Agent 能依赖什么

| 数据形态 | 回答什么 | 入口 |
|---|---|---|
| Document | 原文在哪 | MinIO；经 `restore_context` |
| Evidence | 证据在哪、原文怎么说 | `search_documents` / `search_evidence` |
| Claim | 抽了什么 / 企业认可什么 | `extracted` 默认不进推理；`validated` 经 `get_relationships` |
| Context Pack | 推理该吃什么（含 `missing[]`） | `get_entity_context`（`pack_version=1.0`） |

### Ontology Semantic Layer

世界模型可查询，靠的是完整语义层，而不是「多几个同义词」：

| 能力群 | 做什么 |
|---|---|
| 术语与身份 | `IdentityService`：别名 / 消歧 / 归一化 → 稳定 `HMD:ENT:*` |
| 层级与扩展 | 上下位、`expand_concept` 加权扩展 |
| 类型化关系 | 药↔靶点↔适应症；GraphDB 关系遍历 |
| 外部挂靠 | SSSOM / BIOS / ChEBI… 挂靠企业主键 |
| 结构化事实 | 带出处与许可的 claim |
| 证据检索 | 混合检索 + Evidence Index（含多模态） |
| Citationware | 证据树与 `restore_context`（许可同源） |
| 企业资产 | OpenMetadata：ELN/LIMS「数据在哪」 |
| 聚合上下文 | Context Pack：`pack_version` + `identity` + `missing[]` |
| 许可与合规 | Tier / entitlement；组件闸门；BIOS ACK |
| 可观测与演进 | Trace 四支柱；feedback → KGCL 候选（不自动改本体） |
| Schema 治理 | LinkML SSOT → OWL / SHACL / Pydantic |

**完整手册**（机制、不变量、读数方法）：见 [`docs/`](docs/index.md)。

```bash
uv sync --extra docs --extra dev
task docs:serve    # http://127.0.0.1:8000
task docs          # mkdocs build --strict
```

命令与**实测数字只维护在本 README**（有 `tests/test_readme.py` 守着）；手册讲为什么，不抄表。
构建入口是 **[Taskfile](Taskfile.yml)**（`task …`）。

### 运行时组件

手册详述：[`docs/architecture/foundation.md`](docs/architecture/foundation.md)。

| 组件 | 角色 |
|---|---|
| Enterprise Ontology（LinkML `hmd_enterprise`） | 世界模型主键 `HMD:ENT:*` |
| BIOS_v3 | 公共 biomedical KG（外部概念，非企业主键） |
| IdentityService | 目录 Normalizer + EntityResolver 的单一句柄 |
| BERN2 + 企业词典 + Zingg | NLU 候选 → Entity Resolution |
| GraphDB Named Graphs | biomedical / ontology / knowledge / provenance / inference |
| Milvus | **Evidence Index**（证据在哪；`entity_ids` = Enterprise ID） |
| OpenMetadata | **Data Context**（资产在哪） |
| IngestQA | 入湖质检：空树 / 降级 / 许可 / `doc_id` 幂等 |

```bash
# 联调栈：Milvus + GraphDB + OpenMetadata（BERN2 profile：macOS→MPS 原生 / Linux→CUDA Docker）
export HMD_BIOS_LICENSE_ACK=poc          # BIOS 全量默认；CI: HMD_BIOS_INIT=subset
# GraphDB 10 Free 无需 license；SE/EE 见 docker/docker-compose.graphdb-license.yml
task foundation:up

uv run hmd foundation resolve "赛沃替尼"             # Rich：命中 + 反查别名全集
uv run hmd foundation resolve "HMPL-504" --json      # 机器可读（含 aliases）
uv run hmd foundation golden --candidate HMPL-504   # Drug→Target→Disease→Evidence→ELN/LIMS
uv run hmd foundation sync                           # YAML → GraphDB + Milvus + OM（幂等，三后端必达）
uv run hmd foundation evolve-mine                    # 候选/跳过；不自动改本体
uv run hmd foundation zingg-run --mode stub-link     # 模糊 matches 本地联调（HMD_ZINGG_*）
uv run hmd foundation golden-eval                    # 多路径 WM 评估
uv run hmd serve --mcp                               # 唯一 Semantic API + MCP
task ontology:validate                               # Ontology-as-Code + Golden Path
task foundation:golden-eval                          # GraphDB(+BIOS)/Milvus/OM，禁止 YAML
# 可选观测总线（默认 HMD_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092）：task obs:up
```

金路径：`DrugCandidate → Target → Disease → Evidence → ELN/LIMS Asset`。策展 YAML 在 `ontology/{entities,dictionary,claims}/`；
入库后查询只走 GraphDB / Milvus / OpenMetadata。配置见 `Settings`（`.env` 前缀 `HMD_`；观测/Zingg 见 `HMD_OBS_*` / `HMD_ZINGG_*` / `HMD_EVOLVE_*`）。图引擎只认 **GraphDB**。

---

## 快速开始

```bash
uv sync --extra docs --extra dev
# 瘦安装（身份 / 抽取，不拉 torch / docling）：uv sync --package hmd-nlu

uv run hmd kb        # 构建知识库并打印统计
uv run hmd demo              # 跑 13 个演示场景（K/W/B 双面；Rich + 可证伪断言）
uv run hmd demo --compact    # 仅 Trace 摘要（对齐 hmd foundation golden）
uv run hmd eval --entitlements MOCK_LICENSED   # 双面：Identity + Literature + Bridge
uv run hmd eval --suite identity,bridge --no-retrieval  # 跳过 ARMS 长跑
uv run hmd foundation golden-eval              # WM 三后端金路径（不并入 eval）
uv run hmd serve     # 起 REST + MCP 服务（:8000）
task check           # ruff + ty + 全量测试
```

`task check` = ruff + ty + 全量测试，共 **705 条测试**（默认跳过需 GraphDB 的 integration）。
Milvus 集成测试需 Docker；**失败不回落**到本地后端。

### 工作区包

根仓是 `uv workspace` 伞项目。代码仍在 `src/biomed_ontology/`；`packages/hmd-*` 声明依赖剖面，便于瘦安装。

| 包 | 职责 |
|---|---|
| `hmd-contracts` | LinkML 生成物 / licensing / alias / GraphClient DTO |
| `hmd-core` | Settings / observability / registry |
| `hmd-ingest` | parse / tree / lake steps / IngestQA |
| `hmd-nlu` | normalize / BERN2 / extract / IdentityService |
| `hmd-kg` | GraphDB / sync / world / biomedical sources |
| `hmd-index` | embed / search / rerank / Evidence Index |
| `hmd-access` | tools / service / CLI / eval |

默认 `uv sync` 拉全栈。身份与抽取剖面：`uv sync --package hmd-nlu`。

### Milvus（Evidence Index，必选）

Milvus 既是文献五列检索后端（词法 = `sparse_lexical`），也是 Foundation 的 **Evidence Index**。失败不回落内存词法。

```bash
task milvus:up                                              # hmd-foundation 子集（etcd/minio/standalone）
uv run hmd index --recreate                                 # 默认 multimodal-bio 五列 + BiomedCLIP 图型
uv run hmd eval --entitlements MOCK_LICENSED                # 同上 embedder + bge-reranker-v2-m3
task milvus:down                                            # 只停 Milvus；全栈用 task foundation:down
```

默认 **multimodal-bio** 五列；`embedder=` 戳记不一致则退出。五列需 `PROXY_MAXVECTORFIELDNUM: "6"`（见 `docker/milvus-standalone.yml`）。
权重解析：**本地 → 选定源 → Gitee 兜底**（`embed.resolve_model`）；`fake` 须 `--allow-fake`。
Milvus 臂不可达列在「未运行的臂」下，**绝不回落到本地后端**。

---

## 分层架构

对外是业界六层栈；仓内实现编号为 L0–L8。详见 [Foundation](docs/architecture/foundation.md) · [分层手册](docs/architecture/layers.md)。

| 业界层 | 本仓库落点 |
|---|---|
| Lakehouse | Iceberg + MinIO + Trino |
| Metadata Catalog | OpenMetadata |
| Scientific KG | GraphDB named graphs |
| Vector / Evidence | Milvus |
| Ontology Services | LinkML + IdentityService + BiomedicalSource |
| AI Context APIs | `hmd serve` MCP/REST |

```
L0 Source        构建期联网拉快照 → 版本化存储（version / license / retrieved_on）
L1 术语层        Concept / Synonym / Xref(SSSOM) / Hierarchy → RDF named graph per source
L2 语义层        LinkML（Biolink 子集 + hmd_enterprise）→ OWL + SHACL + JSON Schema + Pydantic
L3 身份          IdentityService：文本 → HMD:ENT:*（目录级联 + ER）
L4 语料治理      Router → 语义树 → IngestQA → 三模态抽取 → 结构化事实 + provenance
L5 检索/证据     BM25 ⊕ dense ⊕ 图通道 → 带权 RRF；Milvus = 五列检索 + Evidence Index
L6 Semantic Access  唯一 REST/MCP：KB 工具 + Foundation Semantic Ops（`hmd serve`）
L7 可观测        Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)
L8 演进闭环      Signal → Candidate → Curation(KGCL) → Release；evolve-mine 不自动改本体
```

**LinkML 是唯一事实来源**（`task gen` → `_generated/`）。机制见 [LinkML 与生成物](docs/architecture/linkml.md)。

---

## Citationware 与可观测（摘要）

检索同时给出 `results` / `evidence_tree` / `restore_context`（许可同源；截断自报 `truncated`）。
四支柱 Trace / IO / State / Metrics 与 Citationware 合成可复核证据链；`trace_id` 闭合 feedback loop。

```bash
uv run hmd demo --id D7
```

详解：[Citationware](docs/tools/citationware.md) · [四支柱](docs/observability/pillars.md)。

---

## 双面 Eval

`hmd eval` = **Identity**（WM resolve 金标）+ **Literature**（归一化 + ARMS）+ **Bridge**（KB∧WM）。  
World Model 三后端金路径另跑 `hmd foundation golden-eval`（[职责对照](docs/eval/dual-surface.md)）。

读数方法、ARMS 定义、显著性纪律见 [双面标准](docs/eval/dual-surface.md) · [ARMS](docs/eval/arms.md) · [显著性](docs/eval/significance.md) · [豁免](docs/eval/targets.md)。
下面只保留**当前实测快照**（有 `tests/test_readme.py` 守着）。

### 归一化（Literature）

本体层 **84 个概念**（43 药 / 21 靶点 / 20 疾病），带双语别名与 `verified: false`。
机制见 [归一化](docs/ontology/normalize.md)。

```
归一化准确率 100.0%  (106/106)
  DISEASE      100.0%  (30/30)
  SUBSTANCE    100.0%  (45/45)
  TARGET       100.0%  (31/31)
  消歧           100.0%  (4/4)
```

`sorafenib` 期望弃权；默认 `min_score=0.60` 避免 n-gram 误配。

### 检索 ARMS（Literature）

`uv run hmd eval --entitlements MOCK_LICENSED`

gold：**14 篇 / 37 query**（en 26 / zh 11；文本 25 / 图像 12），每条带 `probe`，
**judged@10 = 1.000**。判定粒度是章节 → **Recall@10 上限 1.000**。

**主 KPI：本体敏感探针**（`bridge_zh` + `alias`，n=9）：

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR |
|---|---|---|---|---|
| 纯 BM25（无本体） | **0.722** | **0.356** | **0.792** | 1.000 |
| 本体增强混合 | 0.667 | 0.333 | 0.749 | 1.000 |

本体敏感探针 nDCG@10 绝对增益 **−0.043**（T1 门槛 +0.05，**未达成，已豁免**）。配对检验不显著（n=9，CI 触零）。

**全部 query（n=37，诊断口径）**

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR | MAP | judged@10 |
|---|---|---|---|---|---|---|
| 纯 BM25（无本体） | 0.428 | 0.232 | 0.494 | 0.676 | 0.428 | 1.000 |
| 纯向量（无本体） | 0.405 | 0.216 | 0.458 | 0.622 | 0.405 | 1.000 |
| 本体增强混合 | **0.466** | **0.249** | **0.535** | **0.730** | **0.466** | 1.000 |

全量 Recall 相对提升 **+8.9%**（ENT 接地后）。这条数字只作回归诊断，**不是产品门槛**。

**分语种**

| 臂 | en Recall | en nDCG | zh Recall | zh nDCG |
|---|---|---|---|---|
| 纯 BM25 | 0.359 | 0.428 | **0.591** | **0.648** |
| 纯向量 | 0.385 | 0.447 | 0.455 | 0.484 |
| 本体增强混合 | **0.433** | **0.502** | 0.545 | 0.613 |

#### 消融阶梯

本体三条参与路径逐条开；机制见 [查询改写 vs 图通道](docs/retrieval/ontology-paths.md)。

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR |
|---|---|---|---|---|
| ① BM25 + DENSE（本体全关） | 0.267 | 0.270 | 0.333 | 0.527 |
| ② + 图通道（仅种子概念） | 0.264 | 0.270 | **0.343** | **0.592** |
| ③ + search-around（沿类型化链接多跳） | 0.265 | **0.276** | 0.340 | 0.555 |
| ④ 仅查询改写（不开图通道） | **0.271** | 0.265 | 0.334 | 0.511 |

#### 按意图拆（文本 n=25 / 图像 n=12）

| 臂 | 文本意图 n=25 nDCG | 图像意图 n=12 nDCG |
|---|---|---|
| 纯 BM25 | 0.693 | 0.078 |
| 本体增强混合 | **0.755** | 0.078 |

#### 配对显著性（ontology_hybrid − bm25_only，10k bootstrap）

| 指标 | 敏感探针 n=9 | 全部 n=37 | 仅文本意图 n=25 |
|---|---|---|---|
| nDCG@10 | -0.043 [-0.129, +0.000] p=1.000 | +0.042 [-0.033, +0.130] p=0.350 | +0.062 [-0.050, +0.188] p=0.350 |
| Recall@10 | -0.056 [-0.167, +0.000] p=1.000 | +0.038 [-0.043, +0.131] p=0.407 | — |
| P@5 | -0.022 [-0.067, +0.000] p=1.000 | +0.016 [-0.032, +0.065] p=0.653 | — |

14 篇 / 3457 切片 / 84 概念上，全量回归哨兵（T2–T4）已转正；T1 探针增益仍为负，KPI 对准机制而非硬拧全量 +10%。

另有 10 个 Milvus 臂；后端不可达时标记为**未运行**并在报告中列名，
**绝不回落到本地后端** —— 回落会让报告里的「Milvus 三列混合」其实是本地 TF-IDF 跑的。

### 交叉编码器精排（bge-reranker-v2-m3）

`uv run hmd eval --entitlements MOCK_LICENSED --reranker bge-reranker-v2-m3`

融合取前 50 → 精排 → top-10。文本意图 n=25：

| 臂 | Recall@10 | P@5 | nDCG@10 | MAP | Recall@50（候选池） | P50 |
|---|---|---|---|---|---|---|
| 纯 BM25 | 0.308 | **0.320** | 0.379 | 0.215 | — | 0.6ms |
| 本体增强混合 | 0.298 | 0.312 | 0.394 | 0.215 | — | 8.7ms |
| ⑤ 纯 BM25 + 精排 | 0.313 | 0.296 | 0.396 | 0.238 | 0.395 | 693ms |
| ⑥ 本体增强 + 精排 | **0.326** | 0.312 | **0.416** | **0.260** | **0.423** | 688ms |

归因拆分（nDCG@10，n=25）：

| | delta | 95% CI | p |
|---|---|---|---|
| 精排单独的贡献（⑤ − BM25） | +0.017 | [-0.056, +0.089] | 0.657 |
| 本体在精排之上**多给的**（⑥ − ⑤） | +0.020 | [-0.016, +0.061] | 0.362 |
| 两者合计（⑥ − BM25） | +0.037 | [-0.055, +0.129] | 0.449 |

zh nDCG@10 **0.311 → 0.420**、Recall@10 **0.317 → 0.388**（n=11）；en 0.322 → 0.306。
P50 ~9ms → **~690ms**（MPS，50 段 × 512 token）。不传 `--reranker` 时两臂标记**未运行**，**不会退化成 NullReranker 顶替**。

机制见 [精排](docs/retrieval/rerank.md)。

### SapBERT 值多少召回

三列混合 − 双列混合（唯一差别为生医稠密列）。真模型（BGE-M3 + SapBERT + Qwen3-VL + BiomedCLIP）：

| | Recall@10 净值（n=37） | n=28 时 | 最早（n=8） |
|---|---|---|---|
| 全部 | **+0.006** | +0.008 | +0.104 |
| 仅 en | **+0.014** | +0.019 | +0.125 |
| 仅 zh | **−0.011** | −0.014 | +0.083 |

`--embedder fake` 下为 **−0.042 / −0.083 / ±0.000**（非 SapBERT，只验证链路）。
真模型 P50 ~160ms（Milvus + MPS）。详见 [嵌入器](docs/retrieval/embedders.md)。

### 第四列：Qwen3-VL 视觉融合

`--embedder multimodal` = BGE-M3 + SapBERT + **Qwen3-VL-Embedding-2B**（Apache-2.0），第四列 `dense_visual`（2048 维）。
语料 14 篇 / **3457 切片**；索引写入 **99 带图切片**（BiomedCLIP 图型）。
解析细节见 [文档管线](docs/architecture/document-pipeline.md) · [资产](docs/parse/assets.md)。

图像意图 n=12，`dense_visual` 单列：

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR |
|---|---|---|---|---|
| 视觉列（只看图） | **0.944** | 0.417 | **0.863** | 0.875 |

`modalities=[IMAGE]` + CT query 前三名 0.555 / 0.483 / 0.477。
四列 − 三列净值（n=37）：**全部 +0.044 / en −0.002 / zh +0.152**。

### 模态通道

`modalities` 槽 → 布尔过滤（Milvus 下推 / 本地候选集过滤）。`SearchHit` 带回 `modality`。

```bash
uv run hmd demo --id D8
```

```
✓ [D8] 看图通道 — 3457 片中带图 99 片；modalities=[IMAGE] 命中 10 条全为图，7 条不过滤时进不了前十
```

图像意图 gold **12 条**（Q26–Q37）。详见 [过滤](docs/retrieval/filters.md)。

### 图型路由与第五列：BiomedCLIP

`figure_type` 标量字段 + BiomedCLIP 零样本；44 张带资产切片分布：

| RADIOLOGY | MICROSCOPY | GROSS_PATHOLOGY | CHART | DIAGRAM | TABLE_IMAGE | OTHER |
|---|---|---|---|---|---|---|
| 8 | 6 | 1 | 14 | 9 | 5 | 1 |

`--embedder multimodal-bio` = 四列 + **BiomedCLIP**（MIT 权重），第五列 `dense_visual_bio`（512 维）。

图像意图 n=12：

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR |
|---|---|---|---|---|
| 视觉列（Qwen3-VL） | **0.944** | **0.417** | **0.863** | **0.875** |
| 生医视觉列（BiomedCLIP） | 0.889 | 0.317 | 0.602 | 0.487 |

五列 − 四列净值（n=37）：**全部 −0.006 / en −0.009 / zh +0.000**。
BiomedCLIP 权重 MIT，模型卡用途声明超出适用范围；`review=pending`，与 PyMuPDF 同一处置，见 [NOTICE](NOTICE)。
详见 [图型](docs/parse/figure-type.md) · [Milvus](docs/retrieval/milvus.md)。

### 指标目标与豁免机制

`data/gold/targets.yaml` 让「没达成」有署名记录。详见 [目标](docs/eval/targets.md)。

| 目标 | 结果 |
|---|---|
| T1 敏感探针 nDCG@10 绝对增益 ≥ +0.05 | ❌ **−0.043，已豁免**（0.792 → 0.749；n=9） |
| T2 nDCG@10 不劣化 | ✅ 达成 **+0.042** |
| T3 P@5 不劣化 | ✅ 达成 **+0.016** |
| T4 MRR 不劣化 | ✅ 达成 **+0.054** |
| T5 引用忠实度 = 1.000 | ✅ 达成 —— **且这条不接受豁免** |

T1 豁免：重解析后探针上本体臂低于纯词法。T5 不可豁免：引用不忠实比召回差更危险。

---

## 服务入口

**唯一** Semantic Access Layer：`hmd serve` 同时挂载 KB 工具（8 个工具）与 Foundation ops；
CLI 构建/评测命令不变。KB 工具共用 `dispatch` 包裹链（契约校验 / 许可过滤 / trace）。

```bash
uv run hmd serve --mcp --port 8000
```

| 入口 | 地址 |
|---|---|
| REST（KB） | `POST /v1/{tool_name}` × 8 |
| REST（Foundation） | `POST /v1/{op}`（resolve / get_entity / get_entity_context / …） |
| OpenAPI | `GET /openapi.json`（KB 契约 + Foundation 路径） |
| MCP | `POST /mcp/`（Streamable HTTP；8 KB + 10 Foundation = 18） |
| 健康 | `GET /health` |

**MCP 不接受客户端自称的凭据。** REST 侧 `X-HMD-Entitlements` 默认忽略，仅当 `HMD_TRUST_ENTITLEMENT_HEADER=true` 时解析。

---

## 采购依据

`registry.procurement_slots()` 列出已建模但未启用的商业源：

| 优先级 | 源 | tier | 作用 |
|---|---|---|---|
| 1 | UMLS | TIER_2 | 跨词表聚合 + 关系 + 语义类型 |
| 2 | 智慧芽 PatSnap | TIER_3 | 全球管线 / 交易 / 专利-药物关联 |
| 2 | 医药魔方 | TIER_3 | 中国注册审评数据与中文术语 |
| 3 | DrugBank | TIER_2 | 药物别名、靶点、DDI、ATC |
| 4 | MedDRA | TIER_3 | 不良事件五级 + 官方中文 |
| — | CLINICAL_IMAGING | TIER_3 | 临床影像语料；MedImageInsight 前置 |

许可分层贯穿全链路。`hmd demo --id D6` / `--id D7` 各有断言。

---

## 目录

| 路径 | 职责 |
|---|---|
| `schema/` | LinkML SSOT（含 `hmd_enterprise`） |
| `ontology/` | 策展 SSOT（entities / dictionary / claims / mappings / catalog） |
| `packages/` | uv workspace 依赖剖面（`hmd-contracts` … `hmd-access`） |
| `Taskfile.yml` | 统一任务入口 |
| `src/biomed_ontology/identity.py` | IdentityService：目录归一化 + ER |
| `src/biomed_ontology/foundation/` | World Model：resolve / sync / bios / Semantic Ops / Context Pack |
| `src/biomed_ontology/lake/` | 入湖 steps / IngestQA / Evidence Index / Iceberg |
| `src/biomed_ontology/registry/` | 数据源注册表 + 许可分层 |
| `src/biomed_ontology/ontology/` | 等价团、ID 分配、发版、RDF |
| `src/biomed_ontology/parse/` | PDF → 语义树（衍生自 knowhere，见 NOTICE） |
| `src/biomed_ontology/embed/` | BGE-M3 + SapBERT + Qwen3-VL + BiomedCLIP，五向量列 |
| `src/biomed_ontology/rerank/` | bge-reranker-v2-m3 交叉编码器精排 |
| `src/biomed_ontology/search/` | 三通道检索 + 带权 RRF + 模态/图型过滤 + Milvus |
| `src/biomed_ontology/tools/` | 8 个工具 + Citationware（KB Semantic Tools） |
| `src/biomed_ontology/service/` | 唯一 REST/MCP 宿主（`hmd serve`） |
| `src/biomed_ontology/observability/` | 四支柱埋点与契约校验 |
| `src/biomed_ontology/evolution/` | 信号挖掘 → KGCL → 发版守门 |
| `src/biomed_ontology/eval/` | 消融评测 + 指标目标 |
| `data/foundation/` | evidence / assets / BIOS 子集等运行投影 |
| `ontology/catalog/` | 文献/检索 ENT 目录 SSOT（`HMD:ENT:*`） |
| `data/gold/` | gold set 与指标目标 |
| `docker/docker-compose.foundation.yml` | GraphDB + OM + Milvus 联调栈 |
| `docs/` | mkdocs-material 完整手册（`task docs:serve`） |
| `tests/` | 契约与不变量测试 |

---

## 核心设计约束

完整清单见 [设计不变量](docs/invariants.md)。

- **Enterprise Ontology ID（`HMD:ENT:*`）是世界模型与身份主键**；BIOS/ChEBI/HGNC 只做 External Concept xref
- **身份走 `IdentityService`**（目录级联 + BERN2 → dictionary → Zingg → xref → ENT）；术语目录只在 `ontology/catalog/`
- **文档不 mint `HMD:ENT:*`**；挂不上的词进 unmapped / 演进信号
- **文献语料**在 `data/corpus/` → Milvus；外部 ID 一律作为 xref 挂靠
- **Milvus = Evidence Index（必选）**；失败不回落；`fake` 需 `--allow-fake`；占位向量必须标 `embedded=false`
- **Knowledge = Claim + Provenance + Evidence**（Knowledge ≠ Truth）
- **IngestQA 与 QualityGate 分开**：前者拦入库，后者拦发版
- **Semantic Ops 隐藏后端**；不对 Agent 默认暴露裸 SPARQL / 原始向量 API
- **别名必须带 scope**，检索扩展行为由 scope 驱动
- **许可分层贯穿全链路**，tier ≥ 2 内容不得进入导出物与训练语料；BIOS 全量需 `HMD_BIOS_LICENSE_ACK`
- **构建期可联网，运行期完全内网离线**
- **RRF 用名次而非分数融合**；**融合不下推到 Milvus**（保住 `explain`）
- **Ontology Evolution 只落候选**（`evolve-mine`），不自动改本体

---

## 许可与出处

本项目 `src/biomed_ontology/parse/` 的语义树构建算法衍生自
[Ontos-AI/knowhere](https://github.com/Ontos-AI/knowhere)（Apache License 2.0），
已按 Apache 2.0 §4(b) 标注全部修改。

**MinerU、PyMuPDF4LLM、Docling、BiomedCLIP 许可义务待法务核实**，登记在
`licensing.COMPONENTS`，`review` 为 `pending` 时启用相关组件会直接抛
`LicenseViolation`。
BiomedCLIP 权重 MIT，但模型卡另有部署用途超出适用范围的声明。

完整出处、修改说明与许可分析见 [NOTICE](NOTICE)。

语料 PDF **不随仓库分发**，由 `task corpus` 在本地各自取得。
