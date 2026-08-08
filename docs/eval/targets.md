# targets 与豁免纪律

源码：`src/biomed_ontology/eval/targets.py`，配置：`data/gold/targets.yaml`。

## 核心设计

> **一条达不到的目标不能被删掉，只能被署名豁免。**

没有这个机制时，红掉的断言只有两条出路：删掉，或调低阈值。两条都会让对外结论悄悄和事实脱节，且事后无从追溯。

反向绊线同样重要：**目标已达成却仍挂着豁免 → 也判失败**（`stale_waiver`）。否则免责声明永远留在文档里，把已解决问题说成尚未解决。

## 豁免何时算数

```text
waived ⇔ waiver 非空 ∧ waiver_owner 非空
```

只写理由、不署名 = 没人为它负责 = 不算豁免。

字段通常还包括 `waiver_review_by`（复查日期），逼迫豁免有保质期。

## 比较方式

| comparison | 含义 |
|---|---|
| `relative_gain` | 相对基线臂的提升比例 |
| `absolute_gain` | 绝对分差（T1：本体敏感探针 nDCG） |
| `not_worse` | 不劣于基线（T2–T4 类） |
| `at_least` | 绝对下限 |

可选 `probes: [bridge_zh, alias]`：只在这些探针的并集上读数（微平均）。
T1 用此字段；全量平均只作诊断。

臂不可用时 outcome 标 `unavailable`，不是当成 0 分达成。

## T1 口径（0.4.0）

主 KPI = `ontology_hybrid − bm25_only` 在 `probe ∈ {bridge_zh, alias}` 上的 **nDCG@10 绝对增益 ≥ +0.05**。

旧口径（全量 Recall@10 相对提升 ≥10%）已退役：图像意图与英文对照 query 把差值往零压，
且与「关键词 + 本体桥接」要证明的机制错位。

## T5 与不可豁免类约束

部分目标（如引用保真 / fidelity 类，以 `targets.yaml` 为准）在产品上是**底线**：允许暂时豁免的是「增益承诺」，不是「可以返回不可溯源垃圾」。具体哪一条 `waiver` 被禁止，以 YAML 与 `tests/test_eval_targets.py` 为准 —— 改 YAML 放宽底线会有测试挡住。

## 如何验证

```bash
uv run pytest tests/test_eval_targets.py -q
# 若仍有豁免：正文必须引用当前数字（test_waiver_text_quotes…）
```
