# biomed-ontology

面向阿斯利华创新药研发的 **Enterprise Biomedical World Model / AI Data Foundation** PoC。

用企业内部实体 ID（`HMD:ENT:*`）锚定候选药、靶点、项目等对象，挂上关系、可引用证据与
ELN/LIMS 资产，经 MCP/REST（`hmd serve`）暴露给仓外 Agent。公共生物医学知识（BIOS 等）是
挂靠层，不是企业主键。

> BIOS provides the biomedical world. Enterprise Ontology provides the company's world.

**不做 Agent 编排**（无意图解析 / 多步 runtime）。本仓库交付的是 Agent 依赖的语义世界与访问面。

### Ontology Semantic Layer（能力面，不是产品口号）

世界模型可查询，靠的是完整语义层，而不是「多几个同义词」：

| 能力群 | 做什么 |
|---|---|
| 术语与身份 | 别名 / 消歧 / 归一化 → 稳定 code 或 Enterprise ID |
| 层级与扩展 | 上下位、`expand_concept` 加权扩展 |
| 类型化关系 | 药↔靶点↔适应症；GraphDB 关系遍历 |
| 外部挂靠 | SSSOM / BIOS / ChEBI… 挂靠企业主键 |
| 结构化事实 | 带出处与许可的 claim |
| 证据检索 | 混合检索 + Evidence Index（含多模态） |
| Citationware | 证据树与 `restore_context`（许可同源） |
| 企业资产 | OpenMetadata：ELN/LIMS「数据在哪」 |
| 聚合上下文 | `get_entity_context`（禁止 YAML fallback） |
| 许可与合规 | Tier / entitlement；组件闸门；BIOS ACK |
| 可观测与演进 | Trace 四支柱；feedback → KGCL 候选（不自动改本体） |
| Schema 治理 | LinkML SSOT → OWL / SHACL / Pydantic |

**完整手册**（机制、事故教训、设计不变量）：见 [`docs/`](docs/index.md)，本地预览：

```bash
uv sync --extra docs --extra dev
task docs:serve    # http://127.0.0.1:8000
task docs          # mkdocs build --strict
```

命令与**实测数字只维护在本 README**（有测试守着）；手册讲为什么，不抄表。
构建入口是 **[Taskfile](Taskfile.yml)**（`task …`），不再维护 Makefile。

### 运行时组件

手册详述：[`docs/architecture/foundation.md`](docs/architecture/foundation.md)。

| 组件 | 角色 |
|---|---|
| Enterprise Ontology（LinkML `hmd_enterprise`） | 世界模型主键 `HMD:ENT:*` |
| BIOS_v3 | 公共 biomedical KG（外部概念，非企业主键） |
| BERN2 + 企业词典 + Zingg | NLU 候选 → Entity Resolution |
| GraphDB Named Graphs | biomedical / ontology / knowledge / provenance / inference |
| Milvus | **Evidence Index**（证据在哪；`entity_ids` = Enterprise ID） |
| OpenMetadata | **Data Context**（资产在哪） |

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
uv run hmd foundation golden-eval                    # 多路径 WM 评估
uv run hmd serve --mcp                               # 唯一 Semantic API + MCP
task ontology:validate                               # Ontology-as-Code + Golden Path
task foundation:golden-eval                          # GraphDB(+BIOS)/Milvus/OM，禁止 YAML
```

金路径：`DrugCandidate → Target → Disease → Evidence → ELN/LIMS Asset`。策展 YAML 在 `ontology/{entities,dictionary,claims}/`；
入库后查询只走 GraphDB / Milvus / OpenMetadata，**禁止 fallback 到 YAML**。配置见 `Settings`（`.env` 前缀 `HMD_`）。**不引入 Jena**。

---

## 快速开始

```bash
uv sync --extra docs --extra dev

uv run hmd kb        # 构建知识库并打印统计
uv run hmd demo              # 跑 12 个演示场景（K/W/B 双面；Rich + 可证伪断言）
uv run hmd demo --compact    # 仅 Trace 摘要（对齐 hmd foundation golden）
uv run hmd eval --entitlements MOCK_LICENSED   # 双面：Identity + Literature + Bridge
uv run hmd eval --suite identity,bridge --no-retrieval  # 跳过 ARMS 长跑
uv run hmd foundation golden-eval              # WM 三后端金路径（不并入 eval）
uv run hmd serve     # 起 REST + MCP 服务（:8000）
task check           # ruff + 全量测试
```

`task check` = ruff + 全量测试，共 **591 条测试**（默认跳过需 GraphDB 的 integration）。
Milvus 集成测试需 Docker；**失败不回落**到本地后端。

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

检索底座按 L0–L8 组织；Foundation 叠加 **Enterprise World Model**（GraphDB + Evidence Index + Data Context）。详见 [Foundation](docs/architecture/foundation.md) · [分层手册](docs/architecture/layers.md)。

```
L0 Source        构建期联网拉快照 → 版本化存储（version / license / retrieved_on）
L1 术语层        Concept / Synonym / Xref(SSSOM) / Hierarchy → RDF named graph per source
L2 语义层        LinkML（Biolink 子集 + hmd_enterprise）→ OWL + SHACL + JSON Schema + Pydantic
L3 归一化 / ER   文本 → CURIE；Foundation：BERN2 候选 → Enterprise ID
L4 语料治理      文档标引分类 + 三模态抽取（文本/表格/图像）→ 结构化事实 + provenance
L5 检索/证据     BM25 ⊕ dense ⊕ 图通道 → 带权 RRF；Milvus = 五列检索 + Evidence Index
L6 Semantic Access  唯一 REST/MCP：KB 工具 + Foundation Semantic Ops（`hmd serve`）
L7 可观测        Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)；dual obs
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
**judged@10 = 1.000**。判定粒度是章节 → **Recall@10 上限 0.837**。

**主 KPI：本体敏感探针**（`bridge_zh` + `alias`，n=9）：

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR |
|---|---|---|---|---|
| 纯 BM25（无本体） | 0.388 | 0.333 | 0.380 | 0.606 |
| 本体增强混合 | 0.358 | **0.356** | **0.458** | **0.778** |

本体敏感探针 nDCG@10 绝对增益 **+0.078**（T1 门槛 +0.05，已达成）。配对检验仍不显著（n=9，CI 跨零）。

**全部 query（n=37，诊断口径）**

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR | MAP | judged@10 |
|---|---|---|---|---|---|---|
| 纯 BM25（无本体） | 0.269 | **0.259** | 0.333 | 0.528 | 0.201 | 1.000 |
| 纯向量（无本体） | 0.233 | 0.238 | 0.305 | 0.470 | 0.181 | 1.000 |
| 本体增强混合 | **0.274** | 0.254 | **0.340** | **0.532** | **0.201** | 1.000 |

全量 Recall 相对提升 **+1.9%**（ENT 接地后），**不再作为产品门槛** —— 只作回归诊断。

**分语种**

| 臂 | en Recall | en nDCG | zh Recall | zh nDCG |
|---|---|---|---|---|
| 纯 BM25 | 0.248 | **0.343** | **0.317** | 0.311 |
| 纯向量 | 0.242 | 0.323 | 0.212 | 0.264 |
| 本体增强混合 | **0.266** | 0.325 | 0.293 | **0.375** |

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
| 纯 BM25 | 0.401 | 0.192 |
| 本体增强混合 | **0.414** | 0.180 |
| ③ + search-around | **0.416** | 0.180 |

#### 配对显著性（ontology_hybrid − bm25_only，10k bootstrap）

| 指标 | 敏感探针 n=9 | 全部 n=37 | 仅文本意图 n=25 |
|---|---|---|---|
| nDCG@10 | +0.078 [-0.076, +0.220] p=0.361 | +0.005 [-0.042, +0.053] p=0.852 | +0.013 [-0.053, +0.078] p=0.719 |
| Recall@10 | -0.029 [-0.161, +0.062] p=0.873 | +0.002 [-0.037, +0.036] p=0.915 | -0.010 [-0.063, +0.032] p=0.762 |
| P@5 | +0.022 [-0.067, +0.111] p=1.000 | -0.005 [-0.049, +0.038] p=0.960 | -0.016 [-0.080, +0.048] p=0.802 |

14 篇 / 588 切片 / 84 概念上 BM25 已接近饱和；KPI 对准机制而非硬拧全量 +10%。

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
语料 14 篇 / **588 切片**（过滤前 695）；**37 图像切片** / **44 带资产切片**（36 图 + 8 表）。
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
✓ [D8] 看图通道 — 588 片中图像 37 片；modalities=[IMAGE] 命中 5 条全为图，3 条不过滤时进不了前十
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
| T1 敏感探针 nDCG@10 绝对增益 ≥ +0.05 | ✅ 达成 **+0.078**（0.380 → 0.458；n=9，配对仍 n.s.） |
| T2 nDCG@10 不劣化 | ✅ 达成 **+0.005** |
| T3 P@5 不劣化 | ❌ **−0.005，已豁免**（Q7 hierarchy 过度扩展；敏感探针上 P@5 反升） |
| T4 MRR 不劣化 | ✅ 达成 **+0.004** |
| T5 引用忠实度 = 1.000 | ✅ 达成 —— **且这条不接受豁免** |

T3 豁免差值来自 Q7；敏感探针 P@5 +0.022。T5 不可豁免：引用不忠实比召回差更危险。

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
| REST（Foundation） | `POST /v1/{op}`（resolve/get_entity/…） |
| OpenAPI | `GET /openapi.json`（KB 契约 + Foundation 路径） |
| MCP | `POST /mcp/`（Streamable HTTP） |
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
| `ontology/` | 策展 SSOT + Ontology-as-Code（entities / dictionary / claims / mappings） |
| `Taskfile.yml` | 统一任务入口 |
| `src/biomed_ontology/foundation/` | World Model：resolve / sync / bios / Semantic Ops |
| `src/biomed_ontology/registry/` | 数据源注册表 + 许可分层 |
| `src/biomed_ontology/ontology/` | 等价团构建、ID 分配、发版、RDF |
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
- **身份走 ER**（BERN2 → dictionary → Zingg → xref → ENT）；术语目录仅 `ontology/catalog/`（`HMD:SUB` 铸造已退役）
- **文献语料**在 `data/corpus/` → Milvus；外部 ID 一律作为 xref 挂靠（供应商中立）
- **Milvus = Evidence Index（必选）**；失败不回落；`fake` 需 `--allow-fake`
- **Knowledge = Claim + Provenance + Evidence**（Knowledge ≠ Truth）
- **Semantic Ops 隐藏后端**；不对 Agent 默认暴露裸 SPARQL / 原始向量 API
- **别名必须带 scope**，检索扩展行为由 scope 驱动
- **许可分层贯穿全链路**，tier ≥ 2 内容不得进入导出物与训练语料；BIOS 全量需 `HMD_BIOS_LICENSE_ACK`
- **构建期可联网，运行期完全内网离线**
- **RRF 用名次而非分数融合**；**融合不下推到 Milvus**（保住 `explain`）
- **Ontology Evolution 一期只落候选**（`evolve-mine`），不自动改本体

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
