# 显著性怎么读

源码：`src/biomed_ontology/eval/stats.py`（`paired_significance`、`Significance`）；调用：`RetrievalEval.significance()`。

## 为什么存在

在 n≈28～37 的 query 规模上，+0.006 nDCG 可能来自真实机制，也可能来自抖动。点估计 alone 会让「本体增强 +0.013」与「什么都没发生」在报表上长得一样。

配对显著性强制把 **95% 置信区间**与 **置换 p 值** 与差值一起报告，使「涨了多少」必须连同「是否可能纯属巧合」一起说出口。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 检验方式 | 配对 bootstrap CI + 配对置换 p | 独立两样本 t 检验 |
| 分布假设 | 不假设正态（IR 差值常长尾、多零） | 小样本 z 检验 |
| 显著定义 | p < 0.05 **且** CI 不跨零 | 只看 p 或只看点估计 |
| 随机性 | 固定种子（`20240501`） | 每次重跑变 p |
| query 对齐 | 只取两臂键交集 | 不同子集臂强行比 |

## 设计与实现

### 两个统计量

| 统计量 | 方法 | 回答的问题 |
|---|---|---|
| 95% CI | 对 per-query 差值 \(\delta_i\) 有放回重采样（默认 10k） | 差值可能有多大/多小 |
| p 值 | 零假设下随机翻转差值符号（置换） | 这么极端差值多容易由巧合产生 |

`Significance.delta` = mean(\(\delta_i\)) = target − baseline 的配对均值。

`Significance.render()` 示例形态：

```text
+0.013  95% CI [-0.008, +0.034]  p=0.412  n=28  (n.s.)
```

### 配对为何关键

两臂跑**同一批 query**。query 难度是最大方差来源；配对消掉这层差异。独立两样本检验在此规模上几乎什么都测不出来。

### 常用对比

| 对比 | 含义 |
|---|---|
| `ontology_hybrid − bm25_only` | 主产品对比（全量 + 敏感探针各一行） |
| probes=`bridge_zh, alias` | **主 KPI** 切片（T1 同口径） |
| `intent=TEXT` | 仅文本 25 条（图像意图上两臂常逐位相同，压区间向零） |
| `⑥−⑤`（rerank 归因） | 精排 vs 本体在精排之上增量 |
| `milvus_hybrid_3col − 2col` | SapBERT 列净值（非显著性块，是绝对差） |

`RetrievalEval._significance_block()` 主指标优先 **nDCG@10**（Recall@10 在本 gold 上有天花板，见 [gold](gold.md)）。

### 读表纪律

| 现象 | 正确解读 |
|---|---|
| 点估计为正，CI 跨 0，p≫0.05 | **方向可记，不得宣称显著提升** |
| 中文大、英文负 | 拆 `by_lang`，不要被 overall 平均骗 |
| 小 n 时巨大正效应 | 扩样本后常缩数量级；以当前 gold 为准 |
| 臂「未运行」 | 不参与对比，不是 0 |
| 全量不显著、探针显著 | 主 KPI 看探针；全量作诊断 |
| 全量显著、探针不显著 | 不得用全量替代理据写产品结论 |

!!! warning "显著性不是免死金牌"
    不显著 ≠ 机制无用；可能是 gold 太小或方差太大。  
    显著 ≠ 可以上线；还要看许可、延迟、[T5 引用忠实度](targets.md)、失败模式。

### 与 T1 的关系

T1 = 本体敏感探针（`bridge_zh` + `alias`）上 `ontology_hybrid − bm25_only` 的 **nDCG@10 绝对增益 ≥ +0.05**（见 [targets](targets.md)）。报表同时给出该切片的配对显著性；**全量不显著不得写成结论**。

### 特殊情形

- 两臂差值全为 0 → p = 1.0（不是 p = 0 的「极显著」）。
- 置换 p 用 +1 平滑，避免报告 `p=0.000`。
- 视觉臂与全量臂 query 集合不同 → `n` 会小于 37；读 `n` 行。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 固定种子 | README 数字不可复现 |
| 显著需 CI 与 p 同向 | 夸大「显著但 +0.001」 |
| 主 KPI 用探针切片 | 用全量误导产品叙事 |
| 不把 unavailable 当差值 0 | 假显著或假不显著 |

失败模式：

- **只截图点估计**：违反读数合同；
- **忽略 TEXT-only 块**：图像 query 稀释检索改造信号；
- **把 SapBERT 净值当显著性**：净值块是绝对差，另看 embedder 是否真为 SapBERT。

## 如何验证

```bash
uv run pytest tests/test_eval_targets.py -q
uv run hmd eval --entitlements MOCK_LICENSED
```

数字只在 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)；本页只教读法。臂定义见 [arms](arms.md)。
