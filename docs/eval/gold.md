# Gold 与判定粒度

源码：`src/biomed_ontology/eval/retrieval.py`（`load_gold`、`eval_retrieval`、`_chunk_key_index`）。  
数据：`data/gold/retrieval.yaml` 等。

## 为什么存在

检索评测若无稳定、可审计的相关性标注，任何「+10% Recall」都不可信。Gold 定义：

- **query** 文本与元数据（语种、意图、探针类型）；
- **相关集** 键为 `doc_id#section`（章节粒度）；
- **grade** 在该章节内对所有切片统一适用。

手册不抄 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md) 里的实测表，只说明**如何写 gold、如何读分、如何避免自欺**。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 判定键 | `doc_id#section`（来自解析产物） | 凭记忆拼 section 名 |
| 切片映射 | 一节 → **全部** chunk_id 列表 | 每节只保留最后一个 chunk（会丢 456/588 类 bug） |
| 一节内 grade | 全部切片同 relevant / non-relevant | 按 chunk 边界人工重标 |
| dangling 键 | **整次评测拒绝出数** | 静默跳过该 query |
| 主指标 | nDCG@10（理想序按 K 截断） | 单独盯 Recall@10（有天花板） |

## 设计与实现

### Gold 键长什么样

判定键是 `doc_id#section`，`section` 必须来自**解析结果**，不能凭记忆拼。

```bash
uv run python scripts/dump_sections.py           # 真实 section 清单
uv run python scripts/dump_sections.py --grep MET
```

`eval_retrieval` 启动时做 **dangling 检查**：gold 里任一 `doc_id#section` 在 KB 索引中无对应切片 → **整次评测抛错、拒绝出数**。一条静默失效的判定会让分母悄悄变小，报表看起来「变好了」。

### 章节粒度与 chunk 索引

`_chunk_key_index(kb)` 构建 `doc_id#section → [chunk_id, …]`：

- 一节正文通常被切成多片；值是**列表**而非单个 id。
- Gold 判定粒度是**章节**：一节内全部切片同 grade。人工审校面对章节，不应追切片边界（否则每次改 chunk 参数都要重标）。

检索命中任一片即视为命中该章节键（在 per-query 评分逻辑中展开）。

### 为什么 Recall@10 有天花板

判定粒度是**章节**：\(|\mathrm{relevant}|\) 常常大于 \(K=10\)。完美检索也拿不到 Recall@10 = 1.0。

`ArmResult.recall_ceiling` = mean(min(10, |rel|) / |rel|)。`hmd eval` 报表单独打一行「Recall@10 上限」。

与 `judged_at_10` **不是一回事**：

| 指标 | 低了说明什么 | 怎么办 |
|---|---|---|
| `judged_at_10` | 语料扩了、标注没跟上；未判定命中按不相关计分母 | **补 gold 标注** |
| `recall_ceiling` | 相关集比 K 大，指标结构上到不了 1.0 | **换主指标（nDCG@10）或接受天花板** |

混在一起看，会把标注齐全的报表误判为「标注没做完」。

### 意图与语种拆分

Gold 覆盖 en/zh、文本/图像意图。`ArmResult` 提供：

- `by_lang`：SapBERT 等英文单语模型不能把「英文涨了、中文掉了」抹平；
- `by_intent`：TEXT（25 条）vs IMAGE（12 条）；混平均会稀释检索改造效果；
- `by_probe`：`bridge_zh` / `alias` / `hierarchy` / `control` / `image` / `license` 等。

**主 KPI** 读 `bridge_zh + alias`（见 [targets](targets.md) T1）；全量平均只作诊断。

### 引用忠实度（gold 侧）

每条 query 上计算 `citation_fidelity`：命中声称的概念是否真出现在该文档切片关联的概念集中。这是 **T5 硬底线**（≥ 1.0，不可豁免），见 [citationware](../tools/citationware.md)。

### 如何维护 gold

1. `dump_sections` 对照写键；
2. 跑 eval，确认无 dangling；
3. 扩样本后重读小样本时代的「巨大提升」结论（常缩一个数量级）；
4. 改解析导致 section 改名 → gold 必须同步，否则整次评测红灯（这是特性，不是阻碍）。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| 键必须可解析到 ≥1 chunk | dangling → eval 拒绝 |
| 一节多 chunk 全映射 | 召回莫名偏低（历史 bug 类） |
| 探针标签与 query 一致 | 主 KPI 读错切片 |
| 图像意图上本体臂可相同 | 全量显著性被压向零（报表已拆 TEXT 意图） |

失败模式：

- **手写 section 路径**：最常见 dangling 原因。
- **只标一篇 PSC 专篇导致 hierarchy 双零**：改标后 P@5 回归（见 targets.yaml T3 豁免说明）。
- **拿 README 旧数对比新 gold**：样本与标注版本必须对齐。

## 如何验证

```bash
uv run python scripts/dump_sections.py
uv run hmd eval --entitlements MOCK_LICENSED
uv run pytest tests/test_eval_targets.py -q
```

读表纪律见 [significance](significance.md)；臂定义见 [arms](arms.md)。实测数字见 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。
