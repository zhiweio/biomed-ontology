# biomed-ontology

面向阿斯利华创新药研发场景的**生物医药语义层数据基座** PoC。

为 AI agent 提供可溯源的检索能力：Ontology 语义层（谁是谁）+ 结构化事实层（发生了什么）+
文档层（在哪说的）+ 质量层（多可信），配套四支柱可观测与本体演进闭环。

**本仓库不包含 AI agent 本身** —— 只构建其消费的数据底座与工具接口。

---

## 快速开始

```bash
uv sync --extra dev --extra rdf --extra ontology --extra parse --extra vector --extra service

uv run hmd kb        # 构建知识库并打印统计
uv run hmd demo      # 跑 8 个演示场景（全部自带断言，不是打印）
uv run hmd eval      # 检索消融 + 指标目标达成情况
uv run hmd serve     # 起 REST + MCP 服务
```

`make check` = ruff + 全量测试，共 **481 条测试**（476 passed + 5 xfailed）。
那 5 条 xfail 不是"暂时跳过"，而是**当前证伪不了的产品承诺**：本体增强的召回提升、
以及别名一致性 demo D1。全部标了 `strict=True` —— 一旦重新成立就会立刻炸掉，
逼人回来删标记，而不是让"曾经声称过的能力"无声地留在文档里。原因见下面的检索评测一节。
其中 9 条 Milvus 集成测试在没有 Docker 时转为 skipped 而非失败 —— 但要注意，
**这批测试长期静默跳过，曾把三个真 bug 藏了整整一个阶段**（写入不 flush、
`hmd index` 崩、切片 ID 跨进程漂移）。要验收 Milvus 路径就必须把容器起起来。

### 可选：Milvus

```bash
make milvus-up                                              # docker compose，standalone 单机版
uv run hmd index --embedder multimodal --recreate           # 写入 588 切片 / 4 向量列，MPS 约 2m43s
uv run hmd eval --milvus --embedder multimodal \
    --entitlements MOCK_LICENSED                            # 十一臂消融（3 本地 + 8 Milvus）
make milvus-down
```

`--embedder` 可选 `fake` / `bge-m3` / `sapbert` / `qwen3-vl` / `dual` / `multimodal`。
索引与评测必须用同一个 —— 集合 description 里盖了 `embedder=` 戳记，对不上直接退出。

`fake` 是确定性哈希向量，够验证**索引、过滤、融合**这些真正容易出错的部分，
CI 因此不必下载 GB 级权重。但它必须显式加 `--allow-fake` 才跑得起来：
一个假嵌入器产出的报表和真报表长得一模一样，多打一个开关是为了让"这不是模型结论"
在命令行历史里留痕。

### 模型权重从哪来

解析顺序是 **本地已有 → 选定的源 → Gitee 兜底**（`embed.resolve_model`）。

```bash
export HMD_MODEL_HUB=modelscope   # 可选 hf（默认）/ modelscope / gitee
uv run hmd index --embedder dual --recreate
```

- **本地优先**：手工放进 `data/cache/models/models/<仓库名>/` 的权重直接生效。
  内网里手动拷权重是常态，应该走"放对位置"而不是"改代码"。
- **Gitee 兜底**：官方源取不到时自动改用 [gitee.com/hf-models](https://gitee.com/hf-models)，
  并**打印实际用了哪个源** —— 权重来源必须可追溯，否则同一份代码在两台机器上
  可能加载到不同模型而报告里看不出来。
  clone 前先验 `git lfs version`：没装 git-lfs 时 clone 照样"成功"，
  但权重是几百字节的指针文本，报错要推迟到 `AutoModel` 加载才出现、且与 LFS 无关。
- 仓库名逐条登记在 `embed._MIRRORS`：各站命名空间彼此独立，猜不出来。
  没登记的直接报错，不去 clone 一个不存在的仓库。

**算力后端自动选择**：`best_device()` 按 **CUDA > MPS（Apple Silicon）> CPU** 挑，
`--embedder` 相关的构造函数都接受 `device=` 显式覆盖。
实测 MPS 与 CPU 的向量余弦 ≈ 1.0、最大逐元素偏差 3.4e-07（float32 舍入噪声），
因此下面引用的消融数字与所用后端无关。

**SapBERT 只走 PyTorch，且必须取 `[CLS]`。** 这里刻意不用 `SentenceTransformer`：
官方权重目录里没有 `modules.json`，sentence-transformers 会自动补一层
**mean pooling**（实测 `pooling_mode: mean`），于是拿到的是另一个模型的向量 ——
不报错，只是悄悄换掉语义。因此改用 `AutoModel` + 显式 `[CLS]` + L2 归一。
ModelScope 上唯一的 SapBERT 是 Xenova 的 ONNX 版、没有 PyTorch 权重，
故在 `_MIRRORS` 里显式登记为 `None`（并有测试守着，防止被"顺手"填回去）。

不加 `--milvus` 时那 6 个臂会明确列在"未运行的臂（后端不可达，非结果）"下，
**不会**退化成本地后端顶替。这条是刻意的：一份写着 Milvus 却实际由本地跑出的数字，
比没有数字更危险。同理，`--milvus` 遇到集合不存在会直接退出而不是静默降级。

> `--embedder fake` 跑出的"生医稠密"列并不是 SapBERT，
> 因此该模式下的 SapBERT 净值不具备模型层面的解释力，只用于验证链路。

---

## 分层架构

```
L0 Source        构建期联网拉快照 → 版本化存储（version / license / retrieved_on）
L1 术语层        Concept / Synonym / Xref(SSSOM) / Hierarchy → RDF named graph per source
L2 语义层        LinkML schema（Biolink 子集）→ OWL + SHACL + JSON Schema + Pydantic
L3 归一化        文本 → 唯一 CURIE（词典 → 规则 → 向量 → LLM 消歧）
L4 语料治理      文档标引分类 + 三模态抽取（文本/表格/图像）→ 结构化事实 + provenance
L5 检索/查询     BM25 ⊕ dense ⊕ 图通道 → RRF 融合；Milvus 四向量列 + 模态过滤；SPARQL 图查询
L6 Agent 接口    MCP + REST（11 个工具），返回体内建 provenance + trace_id + license_tier
L7 可观测        Trace(WHERE) / IO(WHAT) / State(WHY) / Metrics(WHEN)
L8 演进闭环      Signal → Candidate → Curation(KGCL) → Release → Impact → 回归守门
```

```mermaid
flowchart LR
    PDF[PDF / 文献] --> P[parse<br/>语义树]
    P --> V[vision<br/>表格·图像融合]
    V --> KB[(KnowledgeBase)]
    ONT[LinkML schema] -->|gen-pydantic| KB
    KB --> S[search<br/>BM25 ⊕ dense ⊕ graph]
    KB --> M[(Milvus<br/>4 向量列)]
    M --> S
    S --> API[AgentApi<br/>11 tools]
    API --> REST[REST /v1/*]
    API --> MCP[MCP /mcp]
    API --> C[restore_context<br/>碎片 → 原文]
    API -.trace/io/state/metrics.-> OBS[可观测四支柱]
    OBS -.signal.-> EVO[演进闭环 KGCL]
    EVO -.new release.-> ONT
```

**LinkML 是唯一事实来源。** 所有 Python 数据模型由 `make gen` 从 `schema/` 生成到
`src/biomed_ontology/_generated/`，该目录不手改、不入 lint。
契约、OpenAPI、MCP 描述符全部从同一份 schema 导出 —— 手写第二份就一定会漂移。

**生成是确定性的。** `make gen` 逐字节幂等：同一份 schema 跑两次，`git diff` 为空。

这不是洁癖。rdflib 每次序列化都给空白节点新标签，
`sh:property [ ... ]` 这些匿名块的排列随之改变，曾经一次 `make gen` 就刷出
**6341 增 = 6341 删**，内容一字未动。后果是生成物**失去可审查性** ——
真实的 schema 变更被淹没在纯重排里，review 只能整块跳过；
工作区又永远是脏的，久了就形成"顺手 `git checkout -- schema/generated`"的习惯，
连带真变更一起丢。

`scripts/canon_ttl.py` 在 `gen-shacl` / `gen-owl` 之后重写生成物：
用 `to_canonical_graph()` 按图同构给空白节点算确定性标签，
再把 `sh:ignoredProperties` 这个语义上是集合、却被写成 RDF list 的字段排序
（图同构会如实保留 list 顺序，而那个顺序来自 Python set 的遍历序）。
N-Triples 不行 —— 它按集合迭代序直接倾倒，不排序。

`tests/test_canon_ttl.py` 守三条性质：已提交的生成物已是规范形式、
重新序列化后规范化能回到同一份字节、以及**规范化不改变图语义**
（最后一条是安全带：一个把文件清空的实现同样"幂等"）。

默认套件只跑每类最小的一份文件：`to_canonical_graph` 是图同构算法，
随空白节点数急剧变慢，全部 10 份要 4 分半（`hmd_agentapi.owl.ttl` 一份就占 79 秒）。
把它塞进 `make check` 的真实后果不是"慢一点"，是大家不再跑 `make check`。
**全量覆盖挂 nightly**：

```bash
make nightly        # = make canon-check，全部 10 份，只判定不落盘，约 50s
```

---

## Citationware：引用优先的 RAG

检索返回的是**高匹配度碎片**。碎片能证明"有这句话"，却证明不了"在什么语境下说的" ——
而临床结论的语境（哪一组、哪个终点、哪次随访）恰恰决定它成不成立。

因此每次检索都同时给出三样东西：

| 产物 | 作用 | 入口 |
|---|---|---|
| `results` | 扁平命中，含 `page` / `section` / `license_tier` / `explain` | `search_documents` |
| `evidence_tree` | 文档 → 章节 → 碎片的聚合视图 | `search_documents` |
| 原文还原 | 拼回整节 + 面包屑 + 原始页码 | `restore_context` |

**为什么要证据树**：扁平列表里同一段落的 5 个碎片看上去像 5 条独立证据，
这种"证据量的错觉"会直接误导判断。树把它们收回一个节点。

**为什么还原要走许可**：`restore_context` 若不校验凭据，就成了一个用碎片 id 换全文的后门。
它复用 `LicenseScope.permits` 这**同一个谓词**，而不是自己再实现一份 ——
各写一份迟早出现"检索看不到但还原看得到"。

```bash
uv run hmd demo --id D7
```

```
✓ [D7] 引用优先：碎片 → 原文
   检索命中 5 条，聚成 3 篇文档：
     DOC:CTGOV.NCT02807415 碎片 1 个 → 章节 1 处：BriefSummary
     DOC:PMC12133497       碎片 3 个 → 章节 2 处：Introduction、Discussion
     DOC:PMC13193915       碎片 1 个 → 章节 1 处：Introduction
   还原 CHK:txt.361514dd1b：A Study of Surufatinib … / BriefSummary p1-1，
        300 字碎片 → 312 字全节（共 1 个碎片，截断=False）
   同级章节可继续查阅：Outcomes
   限长 60 字时：truncated=True，实际返回 60 字
   受限文档 DOC:PATSNAP.PS-2023-00417：无凭据还原 0 字（LICENSE_DENIED） /
        有凭据还原 354 字
```

截断会**自报**（`truncated: true`）。静默丢内容会让"还原完整原文"变成一句假话。

---

## 四支柱可观测 ↔ Citationware

两者不是两套东西：Citationware 回答"这句话从哪来"，四支柱回答"这个答案怎么得出的"。
合起来才构成一条可复核的证据链。

| 支柱 | 问题 | 落点 | 在 Citationware 中的角色 |
|---|---|---|---|
| **Trace** | WHERE | `TraceContext.span_tree()` | 哪个通道召回了这条碎片、RRF 各通道名次 |
| **I/O** | WHAT | `ToolIoRecord` | 请求与返回体逐字留档，含 `license_filtered_count` |
| **State** | WHY | `DecisionRecord` | 标题层级判定、消歧选择的候选集与理由 |
| **Metrics** | WHEN | `ArmResult` / `MetricTarget` | 引用忠实度、召回、时延随发版的走向 |

`trace_id` 随返回体回传 agent，`submit_feedback` 以它为主键 ——
**这就是 data loop 的闭合点**：一次错误结论能定位到具体哪一行别名、哪一次扩展决策。

---

## 归一化评测

`hmd eval` 先跑归一化再跑检索。本体层已按语料同步扩到
**84 个概念**（43 药 / 21 靶点 / 20 疾病），全部带双语别名、`xref_hints` 与
`verified: false` 标记 —— 收录范围是"9 篇真实文献正文里出现的主要实体"，
不是"gold query 会问到的实体"。后者会让归一化评测变成自证。

```
归一化准确率 99.1%  (105/106)
  DISEASE      100.0%  (30/30)
  SUBSTANCE     97.8%  (44/45)
  TARGET       100.0%  (31/31)
  消歧           100.0%  (4/4)
    ✗ 'sorafenib' 期望 None 实得 HMD:SUB:0000008
```

**那一条红的是故意留的。** sorafenib 是真实存在但本语料未收录的药，正确行为是弃权；
实际被向量级以 0.57 判成了 regorafenib —— 两者都是 `-afenib` 类抗血管生成 TKI，
编辑距离近而适应症完全不同，是本领域最典型的一类误判。
挑一批一定过的负例凑数，等于把这类错误从报告里抹掉。

## 检索评测

`uv run hmd eval --entitlements MOCK_LICENSED`

gold set 覆盖**全部 14 篇文档 / 28 条 query**（en 19 / zh 9，含 3 条图像意图），
**judged@10 = 1.000** —— 前十里每一条命中都被判定过。
因此下面的数字是测量值，不再是下界。这一点值得单独说：上一版 README 里那句
"这些数字是下界"曾是真的（judged@10 只有 0.238），它现在被删掉不是因为结论变好看了，
而是因为**造成它的原因被消除了**，而消除之后结论反而更难看。

> **但 Recall@10 的上限是 0.800，不是 1.000。**
> gold 的判定粒度是章节：一节内全部切片同 grade，于是 `|relevant|` 常常大于 K=10。
> 完美检索也拿不到 1.0。`hmd eval` 会把这个上限单独打一行 ——
> 它和 judged@10 是两回事，且处置完全相反：judged 低要补标注，上限低只能换指标
> （nDCG@10 的理想序已按 K 截断，不受影响）。混在一起看，
> 会把一份标注齐全的报表继续当成"标注没做完"。

gold 的键是 `doc_id#section`，section 名来自解析结果，凭记忆写必然拼错。
`scripts/dump_sections.py` 把每篇的真实 section 清单打出来供照抄
（`--grep MET` 可按正文关键词筛）；写错的键会被 `eval_retrieval` 的 dangling 检查拦下，
整份评测拒绝出数 —— 不是跳过那一条，因为一条静默失效的判定会让分母悄悄变小。

**全部 query（n=28）**

| 臂 | Recall@10 | P@5 | nDCG@10 | MRR | MAP | judged@10 |
|---|---|---|---|---|---|---|
| 纯 BM25（无本体） | **0.335** | **0.314** | **0.402** | 0.626 | **0.246** | 1.000 |
| 纯向量（无本体） | 0.285 | 0.286 | 0.376 | 0.582 | 0.230 | 1.000 |
| 本体增强混合 | 0.317 | 0.286 | 0.387 | **0.643** | 0.225 | 1.000 |

**分语种** —— 只报总平均会把结论抹平：

| 臂 | en Recall | en nDCG | zh Recall | zh nDCG |
|---|---|---|---|---|
| 纯 BM25 | 0.310 | **0.413** | **0.388** | **0.380** |
| 纯向量 | 0.298 | 0.400 | 0.259 | 0.323 |
| 本体增强混合 | **0.324** | 0.404 | 0.302 | 0.353 |

本体增强臂的 Recall 相对提升是 **-5.2%**（目标 +10%）。这一版能把差额拆开：

| 通道组合 | Recall@10 | nDCG@10 |
|---|---|---|
| 纯 BM25 | 0.335 | 0.402 |
| BM25 + DENSE（不含图通道，不开层级扩展） | 0.333 | 0.411 |
| BM25 + DENSE（不含图通道，**开**层级扩展） | 0.333 | 0.411 |
| BM25 + DENSE + GRAPH（不开层级扩展） | 0.315 | 0.386 |
| 本体增强混合 = 三通道 + 层级扩展（现行配置） | 0.317 | 0.387 |

三条结论：**(a)** 本体今天只经由 GRAPH 一个通道起作用，而该通道净值 **-0.018**；
**(b)** `expand`（层级扩展）对总分的贡献是 **+0.002** —— 它只在 GRAPH 内部展开下位概念，
从不改写 BM25/DENSE 的查询串，所以"本体增强"这个臂名今天名不副实；
**(c)** 剩下的差额来自 DENSE 通道本身，与本体无关。

按语料来源拆，符号是反的：**真实文献 20 条 +5.4%，早期手写构造 8 条 -16.4%**，
总均值为负全部由后者贡献。构造文档每篇只有 1–5 个单切片章节，`|relevant|` 是 1–4，
挤掉一个名次就掉 0.25–0.50；而那些 query 本来就是照着文档正文写的，
BM25 第一名命中，任何重排都只会让它变差。

**不接受"调低阈值"或"删掉构造样本"这两种修法。** 前者是让要求迁就实现；
后者是删掉唯一一批本体臂打不过 BM25 的证据 —— 那 8 条恰恰是最该留的。
真正的修法在检索侧：GRAPH 通道在 84 个概念下几乎每个切片都能挂上，判别力已经稀释，
却仍以整通道权重参与 RRF；且要让层级扩展真的作用到词法/向量通道，而不是只在图里打转。

另有 8 个 Milvus 臂（lexical / general / biomed / 2col / 3col / 4col / visual-only /
ontology+milvus）。后端不可达时它们被标记为**未运行**并在报告中列名，
**绝不回落到本地后端** —— 回落会让报告里的"Milvus 三列混合"其实是本地 TF-IDF 跑的，
这种错误一旦进了采购决策文档就再也追不回来。

### SapBERT 值多少召回

问题是"要不要为生医专用塔多付一列存储和一次前向"，
所以答案必须是个减法：**三列混合 − 双列混合**，两臂唯一差别就是那一列。

真模型（BGE-M3 + SapBERT + Qwen3-VL，n=28）：

| | Recall@10 净值 | 上一版（n=8） |
|---|---|---|
| 全部 | **+0.008** | +0.104 |
| 仅 en | **+0.019** | +0.125 |
| 仅 zh | **−0.014** | +0.083 |

**这张表最重要的一列是右边那列。** 模型没换、列没换、减法定义没换 ——
变的只是 gold 从 8 条扩到 28 条。于是 +0.104 缩到 +0.008（少一个数量级），
zh 从 +0.083 翻成 −0.014。原来那个"值得上"的结论，是 8 条 query 撑出来的。

按现在的数读：**en 仍是正的、zh 已经是负的**，方向与先验一致
（SapBERT 是 UMLS 英文同义词对训练的单语模型）。上一版据此写下的
"不做按语种路由"现在**失去了依据** —— 但也不足以反过来支持路由：
zh 只有 9 条 query，−0.014 落在抖动范围内。
正确的表述是"这一列在中文上没有证据表明有用"，而不是"有害"。
决策待 gold 的中文侧扩到可判定的规模后再做。

⚠️ 同一组指标在 `--embedder fake` 下是 **−0.042 / −0.083 / ±0.000** —— 符号与英文侧相反。
fake 的"生医稠密"列根本不是 SapBERT，只是确定性哈希。
所以 `hmd eval` 的输出会在标题上标注 `embedder=`，
并在非 `sapbert` / `dual` / `multimodal` 时追加一行显式免责 ——
假嵌入器的数字不得被当成模型结论。

代价：真模型下单查询 P50 从 ~0.6ms（本地 BM25）升到 ~160ms（Milvus + MPS 编码）。

### 第四列：Qwen3-VL 视觉融合

非结构化文档里的图表不是装饰 —— 生存曲线、CT 影像、瀑布图常常是结论本身所在。
把它们只留一句 caption 进索引，等于把证据丢了。

`--embedder multimodal` = BGE-M3 + SapBERT + **Qwen3-VL-Embedding-2B**（Apache-2.0，
经 ModelScope 获取），落成第四列 `dense_visual`（2048 维，COSINE / HNSW）。
文本切片这一列编码它的文字，图像切片编码 **像素 + caption**，同处一个空间。

解析侧配套改动（都是真实 PDF 才暴露出来的）：

- PyMuPDF 后端此前把 `type == 1` 的图像块**直接丢弃**（一个没做完的 TODO），
  36 张图一张都进不了库。现在按最小边/面积过滤掉页眉 logo，
  再把相邻子图合并成一张（一页 6 联 CT 会被正确合成 1 张而不是 6 张碎片）。
- `find_tables` 曾把一整页综述正文框成 16 列 × 25 行的"表"，单格 4KB 散文、
  渲染出来 226KB，直接撑爆 Milvus 的 VARCHAR 上限（8192 **字节**，中文一字 3 字节）。
  现在按"单格过长"和"单元格大量重复"两个指纹拒绝误检，落回正文路径。
- 长表按行切片、表头逐片重复：一条向量表达不了一张几十行的表，那只是个平均。
- 参考文献 / 署名 / 利益冲突 / 缩写表等**包装纸章节不进检索**（588 vs 未过滤的 695 切片）。
  它们和查询词汇高度重合，却永远不是答案。

实测（14 篇文档 / 588 切片，索引全量 2m43s，MPS）。
先把口径说清楚，因为这里有两个数容易被当成同一个：
**图像切片 37 片**（modality=IMAGE），**带渲染资产的切片 44 片**（36 图 + 8 表）。
视觉列对这 44 片编码像素，其余切片编码文字。上一版 README 里的"44 张图"是错的 ——
它把 8 张表算成了图。

| 查询 | 视觉列首位 | 分数 |
|---|---|---|
| Kaplan-Meier overall survival curve | **图像切片**（真实生存曲线图） | 0.709 |
| chest CT scan showing a pulmonary nodule | 文本切片 | 0.745（最佳图像 0.427） |

结论要说全：**视觉列确实能把图排到第一，但存在模态间隙**。
文本-文本相似度系统性高于文本-图像，所以当语料里有一段把图讲得很清楚的正文时，
正文会赢。这不一定是错的（那段正文可能确实更好），
但"我要看那张图"是另一类意图，得靠下一节的模态过滤才拿得到。

四列 − 三列的净值（混排场景，n=28）：**全部 +0.053 / en −0.010 / zh +0.186**。
中文增益远大于英文，与 SapBERT 那一列正好相反 ——
图像里的文字标注和坐标轴多为英文，中文 query 在纯文本通道上本来就吃亏，
视觉列等于给它补了一条绕过语言的路。

`hmd index` / `hmd eval` 拒绝 `fake` 嵌入器（除非显式 `--allow-fake`），
且集合的 description 里盖了 `embedder=` 戳记，
索引与评测用的模型对不上时直接退出 —— 上面那个符号相反的教训只需要吃一次。

### 模态通道：「我就要看图」

模态间隙不是靠调分数补的。文本-文本相似度系统性高于文本-图像，
想让图浮上来就得引入一个说不清的跨模态偏置项；
而"我要看那张生存曲线"本来就是个**布尔条件**，不是偏好。
所以它落成过滤：契约里的 `modalities` 槽 → `AgentApi.search_documents` →
`RetrievalRequest` → 两个后端各自执行（Milvus 下推成 `modality in [...]`，
本地后端在候选集上过滤），GRAPH 通道的产物也走同一道过滤。

```python
api.search_documents(query="Kaplan-Meier overall survival curve", modalities=["IMAGE"])
```

`SearchHit` 随之带回 `modality` 字段 —— 不回传的话，调用方无从验证过滤真的生效了。

```bash
uv run hmd demo --id D8
```

```
✓ [D8] 看图通道
   语料 588 片中图像切片 37 片（6.3%）
   不过滤时前十模态构成：{'IMAGE': 2, 'TEXT': 8}
   modalities=[IMAGE] 时命中 5 条，全部为图：
     [IMAGE] DOC:PMID.32821245  p6 :: Kaplan-Meier curve of progression-free survival
     [IMAGE] DOC:PMC13116735    p6 :: Figure 2. Overall survival probability over time
     [IMAGE] DOC:PMC13052964    p4 :: Figure 2. Distribution of AEs over time
     … 另 2 条
   其中 3 条在不过滤时进不了前十
   无凭据 + modalities=[TEXT] 时命中受限文档 0 条（应为 0）
```

评测侧新增 `milvus_visual_only` 臂（只看 `dense_visual` + `IMAGE`）。
它只跑 gold 里标了 `modality_intent: IMAGE` 的 3 条 query ——
把它放到全部 28 条上跑没有意义：其余 25 条要的根本不是图，
一个只返回图的臂在那些 query 上必然是 0，均值会被稀释成一个看不出所以然的数。
`hmd eval` 因此会在表下**显式标出哪些臂跑的是子集**（`n=3` vs `n=28`），
防止有人把 0.889 和上面那些 0.3 横着比。

两条边界必须一起说：

- **过滤保证模态，不保证正确。** 上面那个 CT 查询加了 `modalities=[IMAGE]` 后
  首位是 0.427 的「Top 30 PT 信号强度」图 —— 是图，但不是 CT。
  过滤把答案的搜索空间缩小了，没有提升空间内的排序质量。
- **模态过滤不计入 `license_filtered_count`。** 那个计数是许可边界的证据，
  混进模态过滤就再也说不清"少的那几条是没权限还是不想要"。
  两个后端在这一点上逐字对齐，有测试守着。

### 指标目标与豁免机制

`data/gold/targets.yaml` 存在的意义是**让"没达成"有地方写**。

没有豁免机制时，一条达不到的断言只有两条出路：删掉，或调低。
两条都会让对外结论慢慢和事实脱节，而且没人记得是什么时候脱的。
这里的做法是：目标照写，达不到就填 `waiver` + `waiver_owner` + `waiver_review_by`，
让"未达成"变成一条**署名的、带理由的、可复审的**记录。

| 目标 | 结果 |
|---|---|
| T1 Recall@10 相对提升 ≥ 10% | ❌ **−5.2%，已豁免**（成因见上：本体只经 GRAPH 一个通道，该通道净值 −0.018） |
| T2 nDCG@10 不劣化 | ❌ **−0.015，已豁免**（同源） |
| T3 P@5 不劣化 | ❌ **−0.029，已豁免**（同源） |
| T4 MRR 不劣化 | ✅ 达成 **+0.017** —— 曾是 −0.125 且带豁免，现已撤销 |
| T5 引用忠实度 = 1.000 | ✅ 达成 —— **且这条不接受豁免** |

T1–T3 的豁免这一版全部重写过。上一版把责任推给标注覆盖，
那条理由现在不成立了（judged@10 = 1.000），所以新豁免直接写明
**"不接受调低阈值，也不接受删掉那 8 条构造样本"** ——
后者恰恰是唯一一批本体臂打不过 BM25 的证据，删掉它等于把问题藏起来。
复审时点写的是"检索侧完成 GRAPH 通道权重与查询改写两项改造后重测"，
在那之前对外只可引用"真实文献子集 +5.4%"，不可引用总均值。

T4 走完了整条路径：写死的 `<= 0` 断言 → 未达成 + 署名豁免 → 达成 + 撤销豁免。
反向绊线同样存在：**目标已达成却还挂着豁免，测试也会失败** ——
那意味着对外结论仍在引用一条过期的免责说明。
另有测试断言豁免正文中引用的数字与当前实测一致，防止理由写完就腐烂；
以及一条守着"T4 别连着豁免一起被删掉"——
一条曾经红过、后来转绿的目标最容易在清理时被顺手删除。

**T5 为什么不可豁免**：召回差只是找不到，用户知道自己没拿到答案；
引用不忠实是把一个看似有据的错误答案递出去，用户没有识别它的手段。
本体扩展天然放大这个风险 —— 扩展出来的概念很容易被顺手记成"原文说的"。

---

## 服务入口

CLI 一个命令不动，REST 与 MCP 是**并列的第二个入口**，三者共用同一个 `dispatch` ——
包裹链（契约校验 / 许可过滤 / trace 留痕）因此无法被绕过。

```bash
uv run hmd serve --port 8000
```

| 入口 | 地址 |
|---|---|
| REST | `POST /v1/{tool_name}` × 11 |
| OpenAPI | `GET /openapi.json`（从契约导出，非反射生成） |
| MCP | `POST /mcp/`（Streamable HTTP） |
| 健康 | `GET /health` |

**MCP 不接受客户端自称的凭据。** REST 侧的 `X-HMD-Entitlements` 头默认也被忽略，
仅当 `HMD_TRUST_ENTITLEMENT_HEADER=true` 时才解析。
把许可边界交给调用方自觉遵守，等于没有边界。

---

## 采购依据

`registry.procurement_slots()` 列出已建模但未启用的商业源，按优先级排序 ——
**槽位先建好，采购决策才有具体的接入成本可谈**：

| 优先级 | 源 | tier | 作用 |
|---|---|---|---|
| 1 | UMLS | TIER_2 | 跨词表聚合 + 关系 + 语义类型。注意 UMLS 内部按 SAB 分 category 0–3，接入时须逐 SAB 映射 tier |
| 2 | 智慧芽 PatSnap | TIER_3 | 全球管线 / 交易 / 专利-药物关联 |
| 2 | 医药魔方 | TIER_3 | 中国注册审评数据与中文术语 |
| 3 | DrugBank | TIER_2 | 药物别名、靶点、DDI、ATC |
| 4 | MedDRA | TIER_3 | 不良事件五级 + 官方中文。许可最严，导出闸门须逐条拦截 |

许可分层贯穿全链路：无凭据时商业源内容在**事实、检索、SPARQL、还原**四处同时不可见。
`hmd demo --id D6`（前三处）与 `--id D7`（还原）对此各有断言。

---

## 目录

| 路径 | 职责 |
|---|---|
| `schema/` | LinkML 模型定义，单一事实来源 |
| `src/biomed_ontology/registry/` | 数据源注册表 + 许可分层 |
| `src/biomed_ontology/ontology/` | 等价团构建、ID 分配、发版、RDF |
| `src/biomed_ontology/parse/` | PDF → 语义树（衍生自 knowhere，见 NOTICE） |
| `src/biomed_ontology/embed/` | BGE-M3 + SapBERT + Qwen3-VL，四向量列 |
| `src/biomed_ontology/search/` | 三通道检索 + RRF + 模态过滤 + Milvus 后端 |
| `src/biomed_ontology/agentapi/` | 11 个 agent 工具 + Citationware |
| `src/biomed_ontology/observability/` | 四支柱埋点与契约校验 |
| `src/biomed_ontology/evolution/` | 信号挖掘 → KGCL → 发版守门 |
| `src/biomed_ontology/eval/` | 消融评测 + 指标目标 |
| `data/gold/` | gold set 与指标目标 |
| `tests/` | 契约与不变量测试 |

---

## 核心设计约束

- **内部 CURIE 是唯一主键**，外部 ID 一律作为 xref 挂靠（供应商中立）
- **别名必须带 scope**，检索扩展行为由 scope 驱动
- **许可分层贯穿全链路**，tier ≥ 2 内容不得进入导出物与训练语料
- **构建期可联网，运行期完全内网离线**
- **RRF 用名次而非分数融合** —— 三通道量纲不可比，归一化会引入说不清的超参
- **融合不下推到 Milvus** —— Milvus 的 RRF 分数无法还原为各通道名次，会毁掉 `explain`

---

## 许可与出处

本项目 `src/biomed_ontology/parse/` 的语义树构建算法衍生自
[Ontos-AI/knowhere](https://github.com/Ontos-AI/knowhere)（Apache License 2.0），
已按 Apache 2.0 §4(b) 标注全部修改。

**MinerU 与 PyMuPDF 两项许可义务待法务核实**，登记在 `licensing.COMPONENTS`，
`review` 为 `pending` 时启用相关后端会直接抛 `LicenseViolation` ——
义务只写进文档没人会读，写成闸门才绕不过去。

完整出处、修改说明与许可分析见 [NOTICE](NOTICE)。

语料 PDF **不随仓库分发**，由 `make corpus` 在本地各自取得。
