# ARMS 消融阶梯（Literature 子套件）

源码：`eval.ARMS`（`src/biomed_ontology/eval/retrieval.py`）。  
编排：双面 Scorecard 的 Literature 段，见 [dual-surface](dual-surface.md)。  
World Model 三后端联调不在此文件——见 `hmd foundation golden-eval`。

## 为什么存在

「本体增强混合检索」不是单一开关。图通道、search-around、查询改写、多列向量、交叉编码器精排可能同时起作用。若只报 `ontology_hybrid` 总成绩，无法回答：

- 涨分来自图倒排还是邻居扩展？
- SapBERT 列是否值得索引成本？
- 精排增益里有多少应归因于本体？

ARMS 把消融臂做成**一等公民配置**（`ARMS` 字典），与 `hmd eval` 同一条代码路径执行，保证 README 数字可重跑、可证伪。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 生产后端 | 全部 `backend: milvus` | Milvus 不可达时内存词法顶替 |
| 图依赖 | `require_graph: true` 的臂需 GraphDB | 图 down 时假装跑通 |
| 精排 | 无 reranker → 臂标「未运行」 | NullReranker 冒充精排 |
| 视觉专用臂 | `modality_intent: IMAGE` 只在图像 query 评分 | 用文本 query 评视觉列 |
| 列净值 | 固定臂对（如 3col−2col） | 口头「加了 SapBERT 好像好点」 |

## 设计与实现

### 主臂：归因阶梯（全部 `backend: milvus`）

| 臂名 | 配置要点 | 回答的问题 |
|---|---|---|
| `bm25_only` | `sparse_lexical`，无 expand | 纯词法基线 |
| `dense_only` | dense 列，无 expand | 纯向量基线 |
| `ontology_hybrid` | 三通道 + expand，`require_graph` | 本体全开（总成绩单） |
| `bm25_dense` ① | BM25+DENSE，无本体 | 融合基线 |
| `bm25_dense_graph` ② | +GRAPH，`expand=False`，`require_graph` | 图通道仅种子概念 |
| `bm25_dense_hops` ③ | +expand，`rewrite=False`，`require_graph` | search-around 净值 |
| `bm25_dense_expand` ④ | 无 GRAPH，expand/rewrite | 仅查询改写净值 |
| `bm25_rerank` ⑤ | 词法 + 精排，`candidate_k=50` | 精排单独贡献的被减数 |
| `ontology_hybrid_rerank` ⑥ | 全开 + 精排，`require_graph` | 本体在精排之上多给的 |

!!! tip "为什么 ⑤⑥ 成对"
    只有 ⑥ 时，涨了也说不清是本体还是精排。减法需要被减数 ⑤。

### 按列减法（`vector_fields`）

| 臂名 | 列组合 | 用途 |
|---|---|---|
| `milvus_lexical` | sparse_lexical | 单列词法 |
| `milvus_general` | dense_general | 通用稠密 |
| `milvus_biomed` | dense_biomed | 生医稠密 |
| `milvus_hybrid_2col` | lexical + general | 双列基线 |
| `milvus_hybrid_3col` | + biomed | SapBERT 净值高位 |
| `milvus_hybrid_4col` | + visual | 视觉列净值（混排） |
| `milvus_hybrid_5col` | + visual_bio | 生医视觉列净值 |
| `milvus_visual_only` | dense_visual，`modality_intent=IMAGE` | 只要图时准不准 |
| `milvus_visual_bio_only` | dense_visual_bio，`modality_intent=IMAGE` | 生医视觉塔 |

固定减法对（`retrieval.py` 常量）：

| 常量 | 臂对 | 回答问题 |
|---|---|---|
| `SAPBERT_DELTA` | `milvus_hybrid_3col` − `milvus_hybrid_2col` | SapBERT 列净值 |
| `VISUAL_DELTA` | `4col` − `3col` | 混排场景视觉列 |
| `VISUAL_BIO_DELTA` | `5col` − `4col` | 生医视觉列增量 |

Milvus 或 GraphDB 不可达 → 臂写入 `RetrievalEval.unavailable`，**不**回落内存词法。

### 开关语义（传给 `HybridSearcher.search`）

| 字段 | 含义 |
|---|---|
| `channels` | BM25 / DENSE / GRAPH |
| `expand` | 是否 search-around 扩展概念 |
| `rewrite` | 是否查询改写（③ 与 ④ 对比关键） |
| `vector_fields` | Milvus 启用哪些向量列 |
| `rerank` + `candidate_k` | 精排候选池大小 |
| `require_graph` | GraphDB 不可达则整臂未运行 |
| `modalities` / `modality_intent` | 限制候选与评分 query 子集 |

`expand=False` 时图通道仍可跑，但**不**做 `neighbors` 扩展（仅种子）。`rewrite=False` 时改写关闭，图仍可 hops——这正是臂 ③ 与 ② 的差别。

### 怎么读阶梯

```text
① → ②：图通道（种子倒排）是正是负？
② → ③：search-around 值多少？
① → ④：改写值多少？（可看 by_lang）
⑤ → ⑥：本体在精排池上的增量
```

总成绩单看 `ontology_hybrid`；机制归因看 ①–⑥。显著性见 [significance](significance.md)。

报表还会输出：

- **候选池召回** `recall_at_pool`（精排上限）；
- **embedder / reranker** 实际名称（fake 下不得当 SapBERT 结论）；
- **query 子集警告**（视觉臂 n≠全量时不可横向比）。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 未运行 ≠ 0 分 | 误读为「做了但很差」 |
| 精排无模型则不跑 | 避免假精排 |
| 图像臂只在 IMAGE intent 评分 | 文本 query 上 0.000 无意义 |
| fake embedder 警告 | 净值只验链路，不支撑采购 |

失败模式：

- **只盯 ontology_hybrid**：说不清机制；
- **用 visual_only 证明混排不值**：两个问题域不同；
- **忽略 unavailable 表**：把没测的配置当测了。

## 如何验证

```bash
uv run hmd eval --entitlements MOCK_LICENSED
uv run pytest tests/test_eval_targets.py tests/test_eval_demo.py -q
```

实测臂表见 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)；目标门禁见 [targets](targets.md)。
