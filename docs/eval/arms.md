# ARMS 消融阶梯（Literature 子套件）

源码：`eval.ARMS`（`src/biomed_ontology/eval/retrieval.py`）。  
编排：双面 Scorecard 的 Literature 段，见 [dual-surface](dual-surface.md)。  
World Model 三后端联调不在此文件——见 `hmd foundation golden-eval`。

消融臂是一等公民配置，不是「某次手工跑完贴进 README」。升格之后，「本体到底经由哪条路起作用」才是可重跑、可证伪的问题。

## 主臂：归因阶梯（全部 `backend: milvus`）

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
    只有 ⑥ 时，涨了也说不清是本体还是精排。减法需要被减数。

## 按列减法

`milvus_lexical` / `general` / `biomed` / `hybrid_2col`…`5col` 等：用 `vector_fields` 控制启用列。  
Milvus 或 GraphDB 不可达 → **未运行**，不回落内存词法。

## 开关语义（实现）

传给 `HybridSearcher.search` 的字段：

- `channels` / `expand` / `rewrite` / `vector_fields` / `rerank`+`candidate_k` / `backend` / `require_graph`

`expand=False` 时图通道仍可跑，但 **不** `neighbors` 扩展（仅种子）。  
`rewrite=False` 时改写关闭，图仍可 hops —— 这正是臂 ③。

## 怎么读阶梯

```text
① → ②：图通道（种子倒排）是正是负？
② → ③：search-around 值多少？
① → ④：改写值多少？（可看 by_lang）
⑤ → ⑥：本体在精排池上的增量
```

总成绩单看 `ontology_hybrid`；机制归因看 ①–⑥。显著性见下一章。

## 如何验证

```bash
uv run hmd eval --entitlements MOCK_LICENSED
uv run pytest tests/test_eval_targets.py tests/test_eval_demo.py -q
```
