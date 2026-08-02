# 交叉编码器精排

源码：`src/biomed_ontology/rerank/`。

## 为什么需要精排

RRF 按名次投票，名次不表达「有多相关」：三通道各自的第 3 名进了融合，谁更该排前面 RRF 没有依据。交叉编码器对 `(query, passage)` 打分，补的就是这个依据。

## 与召回的边界

精排**不能**找回没进池子的相关文档。所以：

- `candidate_k`（默认精排臂 50）> `top_k`  
- 评测要同时有 `bm25_rerank` 与 `ontology_hybrid_rerank` —— 只有后者时，涨了也说不清是本体还是精排  

归因拆法（读 README 表时）：

```text
精排单独贡献 ≈ ⑤ − BM25
本体在精排之上多给的 ≈ ⑥ − ⑤
合计 ≈ ⑥ − BM25
```

## 实现纪律

1. **不可达 → 未运行**，不得退化成恒等 `NullReranker` 还标注「+精排」。  
2. **保留 `rank_before_rerank`** —— 否则无法回答「谁从第 23 拉到第 2」。  
3. **精排同分时的次级键**是融合名次，不是 `chunk_id`（避免哈希序复活）。  
4. **FlagEmbedding 的坑**：部分版本 `compute_score` 依赖 `tokenizer.prepare_for_model()`，与当前 transformers API 不兼容。本仓库走 transformers 正道加载，避免静默坏分。

## 延迟

交叉编码器是热路径上最贵的一步（README 有数量级对比）。PoC 默认评测可开；在线服务是否默认开精排，是产品权衡，不是算法必然。

## 如何验证

```bash
uv run hmd eval --reranker bge-reranker-v2-m3 --entitlements MOCK_LICENSED
uv run pytest tests/ -k rerank -q
```
