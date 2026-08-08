# 可观测四支柱

源码：`src/biomed_ontology/observability/`。

## 四个问题

| 支柱 | 问什么 | 主要类型 |
|---|---|---|
| Trace (WHERE) | 这次调用经过哪些阶段 | `Span` / `TraceContext` |
| I/O (WHAT) | 进出内容是什么 | `ToolIoRecord` |
| State (WHY) | 为什么选这个、落选的是谁 | `DecisionRecord` + `Candidate` |
| Metrics (WHEN) | 指标随时间与 release 怎么变 | `MetricPoint` |

State 最容易被省掉，排障时却最不可替代：只记结果不记候选，永远回答不了「为什么没选那个」。

## 设计：采集契约与后端可换

本模块**不**绑死 OTel SDK / OpenSearch。内存 + JSONL 落盘即可跑 PoC；部署时替换 recorder/store，**被埋点的业务代码一行不用改**。

属性键沿用 OTel 语义 + `ontology.*` / `hmd.*` 扩展（如 `hmd.query`、`ontology.concept_ids`）。

## 与业务同次写入

归一化级联的中间候选在函数返回后消失 —— 后补埋点只能拿到最终结果。因此 `Normalizer` / `HybridSearcher._graph_channel` / `ToolApi._invoke` 在决策点当场 `record_decision`。

## Tool 包裹链中的位置

见 [Semantic Access](../tools/tools.md)：`_invoke` 强制起 trace、落 I/O。`trace_id` 随返回体回传，反馈接口以它为主键（D6）。

## Explain 是 WHY 的用户可见面

`SearchHit.explain = RRF(bm25#3 + …)` 把通道名次暴露给调用方；融合若下推到无法反解的后端，这一支柱在检索路径上断裂。

## 如何验证

```bash
uv run pytest tests/test_observability.py -q
```
