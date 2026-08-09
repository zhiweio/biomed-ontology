# targets 与豁免纪律

源码：`src/biomed_ontology/eval/targets.py`  
配置：`data/gold/targets.yaml`（版本 `0.5.0`）。

## 为什么存在

检索评测若只有「好看的数字」，发版时红掉的断言会被悄悄删掉或阈值调低，对外结论与事实脱节且无从追溯。

targets 机制的核心设计：

> **一条达不到的目标不能被删掉，只能被署名豁免。**

反向绊线同样重要：**目标已达成却仍挂着豁免 → 也判失败**（`stale_waiver`）。否则免责声明永远留在文档里，把已解决问题说成尚未解决。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 未达成 | `waiver` + `waiver_owner` 署名 | 删目标或改阈值 |
| 豁免有效性 | 理由与署名人缺一不可 | 只写理由无人负责 |
| 复审 | `waiver_review_by` 迫近保质期 | 永久豁免 |
| 臂不可用 | `unavailable=true`，不参与「达成」 | 当成 0 分 |
| 底线指标 | T5 `citation_fidelity` 不可豁免 | 用 waiver 掩盖造引用 |

## 设计与实现

### 配置结构（`MetricTarget`）

| 字段 | 含义 |
|---|---|
| `id` | 如 T1–T5 |
| `metric` | `ndcg_at_10`、`citation_fidelity` 等 |
| `arm` / `baseline_arm` | 读数臂与基线臂 |
| `comparison` | 见下表 |
| `threshold` | 判定阈值 |
| `probes` | 可选；探针并集上微平均 |
| `lang` | 可选；语种切片 |
| `waiver` / `waiver_owner` / `waiver_review_by` | 豁免三元组 |

### 比较方式（`comparison`）

| comparison | 含义 | observed 计算 |
|---|---|---|
| `relative_gain` | 相对基线提升比例 | (actual − baseline) / baseline |
| `absolute_gain` | 绝对分差 | actual − baseline |
| `not_worse` | 不劣于基线 | actual − baseline ≥ 0 |
| `at_least` | 绝对下限 | actual ≥ threshold |

`check_targets(ev, targets)` 返回 `TargetOutcome`：`met`、`waived`、`stale_waiver`、`unavailable`、`explain()` 文案。

### Suite 硬门禁（不在 targets.yaml 的 arm 比较器里）

文档化于 `targets.yaml` 的 `suite_gates`，实现于 `DualEvalReport`：

| ID | 套件 | 规则 |
|---|---|---|
| I1 | identity | `gate_accuracy == 1.0` |
| B1 | bridge | `alias_ok ∧ literature_ok ∧ entitlement_ok` |

### T1–T5 口径（0.4.0+）

| ID | 指标 | 口径摘要 | 状态要点 |
|---|---|---|---|
| **T1** | nDCG@10 `absolute_gain` ≥ +0.05 | `ontology_hybrid − bm25_only`，**仅** `probes: [bridge_zh, alias]` | 主 KPI；旧全量 R@10 +10% 已退役 |
| **T2** | nDCG@10 `not_worse` | 全量回归哨兵 | 已达成 |
| **T3** | P@5 `not_worse` | 全量；Q7 hierarchy 过度扩展证据 | **已署名豁免**（见 YAML 全文） |
| **T4** | MRR `not_worse` | 全量 | 已达成 |
| **T5** | `citation_fidelity` `at_least` 1.0 | 全臂；**不接受豁免** | 造引用 = 缺陷修掉，不能签 waiver |

T3 豁免说明（摘要）：差值主要来自 hierarchy 探针 Q7——宽泛上位词上本体扩展冲淡精排前排；**主 KPI 切片 P@5 反而上升**，退化集中在「该不该扩」的层级探针。复审前对外不得把全量 P@5 写成「本体不伤精排」。

### 与 Literature 报告的关系

`run_dual_eval` 在 Literature 跑完后调用 `check_targets` → `literature_targets`。`DualEvalReport.literature_ok` 要求：

- 每个非 `unavailable` 的 outcome：`met or waived`，且无 `stale_waiver`；
- 所有已跑臂 `citation_fidelity >= 1.0`（T5 加强）。

### 豁免何时算数

```text
waived ⇔ waiver 非空 ∧ waiver_owner 非空
```

只写理由、不署名 = 没人为它负责 = 不算豁免。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| T5 无 waiver 字段 | 测试与产品底线 |
| stale_waiver 失败 | 文档与事实不一致 |
| unavailable 不当 0 | 假绿或假红 |
| 改 YAML 放宽 T5 | `test_eval_targets` 拦截 |
| 豁免正文须引当前数 | `test_waiver_text_quotes…` |

失败模式：

- **删掉翻红目标**：破坏可追溯性；
- **达成后忘撤 waiver**：stale_waiver；
- **用全量 T2–T4 替 T1 叙事**：与产品定位错位；
- **citation 破了仍发版**：T5 + literature_ok 双杀。

## 如何验证

```bash
uv run pytest tests/test_eval_targets.py -q
uv run hmd eval --entitlements MOCK_LICENSED
```

读显著性见 [significance](significance.md)；实测表见 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
