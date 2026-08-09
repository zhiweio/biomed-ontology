# 可观测四支柱

源码：`src/biomed_ontology/observability/`（`__init__.py`、`contracts.py`）。  
消费方：归一化级联、`HybridSearcher`、`ToolApi._invoke`、演进挖掘（`evolution/`）。

## 为什么存在

语义层排障不能只靠「最终返回了什么」。典型问题：

- 为什么选了概念 A 而不是 B？
- 混合检索里 BM25 与图通道各贡献了几条？
- 许可过滤掉了多少候选？
- 某次发版后 nDCG 掉了，是模型还是本体 release 变了？

四支柱分别回答 **WHERE / WHAT / WHY / WHEN**。其中 **State（WHY）** 最容易被省掉，却最不可替代：只记结果不记候选，永远答不了「为什么没选那个」。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 后端绑定 | 采集契约 + 内存 / JSONL；可换 OTel / Iceberg | 业务代码直接依赖 OTel SDK |
| 属性命名 | OTel 语义 + `ontology.*` / `hmd.*` 扩展 | 各模块私有字段名 |
| 决策记录时机 | 决策点当场 `record_decision` | 函数返回后补埋点（候选已消失） |
| Explain | `SearchHit.explain` 暴露 RRF 通道名次 | 融合下推后无法反解 |
| trace 回传 | `trace_id` 随 tool 响应返回（D6） | 仅服务端日志可见 |

## 设计与实现

### 四个问题

| 支柱 | 问什么 | 主要类型 |
|---|---|---|
| Trace (WHERE) | 这次调用经过哪些阶段 | `Span` / `TraceContext` |
| I/O (WHAT) | 进出内容是什么 | `ToolIoRecord` |
| State (WHY) | 为什么选这个、落选的是谁 | `DecisionRecord` + `Candidate` |
| Metrics (WHEN) | 指标随时间与 release 怎么变 | `MetricPoint` |

`TraceContext` 承载一次 tool 调用的完整上下文：`ontology_release_id`、`entitlements`、span 栈、`decisions` 列表。`span()` 上下文管理器自动记录耗时与错误属性。

### `DecisionRecord` 结构

| 字段 | 含义 |
|---|---|
| `stage` | 归一化阶段 / 检索阶段等 |
| `justification` | `MappingJustificationEnum`（规则 / 词典 / 模型等） |
| `chosen` | 选中项 id |
| `candidates` | 落选候选（含 score、channel、label） |
| `state_before` / `state_after` | 级联状态机跃迁 |
| `rule_id` / `model_id` | 可归因的规则或模型 |

写入点示例：`Normalizer` 级联、`HybridSearcher._graph_channel`、`ToolApi._invoke`。

### `ToolIoRecord`

记录每次工具调用的输入/输出 JSON、延迟、`contract_valid`、`license_filtered_count`、`max_tier_returned`、`caller_entitlements` 等，供审计与演进挖掘。

### 与 Semantic Access 的衔接

见 [tools](../tools/tools.md)：`ToolApi._invoke` 强制起 trace、落 I/O。`submit_feedback` 以**被评价调用**的 `trace_id` 为主键，把用户纠正挂回当时的决策与候选。

### Explain 与 WHY 的用户可见面

`SearchHit.explain = RRF(bm25#3 + …)` 把通道名次暴露给调用方。若融合完全下推到无法反解的后端，检索路径上的 WHY 支柱断裂——因此混合检索保留可解释的 RRF 融合层。

### 消费正确性：`citation_fidelity`

`observability/contracts.citation_fidelity` 校验 agent 声称引用的文档是否在返回集内，且概念归因是否匹配。用于评测与质量闸门，见 [citationware](../tools/citationware.md)、[targets](../eval/targets.md) T5。

### 存储与扩展

- PoC：`ObservabilityHub` + `JsonlStore` 本地落盘。
- 部署：替换 `TraceRecorder` / store 实现（如 OTel exporter、`biomed_ontology.lake`），**被埋点业务代码不改**。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 决策点同步写入 | 无法回放「为什么」 |
| `trace_id` 回传客户端 | 反馈无法挂靠 |
| span 树与 release_id 绑定 | 跨版本对比失真 |
| Explain 可反解 | 检索黑盒 |
| hub 跨请求共享（`ServiceState`） | 演进挖掘不到信号 |

失败模式：

- **只开 Trace 不开 Decision**：排障仍靠猜。
- **每请求新建 hub**：`hmd signals` 挖不到历史。
- **契约校验失败仍记 OK**：`ToolIoRecord.contract_valid=false` 应告警。

## 如何验证

```bash
uv run pytest tests/test_observability.py -q
uv run hmd demo --compact    # 查看 span 树
uv run hmd signals --help    # 挖掘依赖 hub / feedback
```

演进闭环见 [loop](../evolution/loop.md)。
