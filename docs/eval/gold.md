# Gold 与判定粒度

源码：`src/biomed_ontology/eval/`，数据：`data/gold/`。

## Gold 键长什么样

判定键是 `doc_id#section`，section 名必须来自**解析结果**，不能凭记忆拼。

```bash
uv run python scripts/dump_sections.py           # 真实 section 清单
uv run python scripts/dump_sections.py --grep MET
```

写错的键会被 `eval_retrieval` 的 **dangling 检查**拦下 —— **整次评测拒绝出数**，不是跳过那一条。一条静默失效的判定会让分母悄悄变小，报表看起来「变好了」。

## 为什么 Recall@10 有天花板

判定粒度是**章节**：一节内全部切片同 grade。于是 \(|\mathrm{relevant}|\) 常常大于 \(K=10\)。完美检索也拿不到 Recall@10 = 1.0。

`hmd eval` 会把这个上限单独打一行。它和 judged@10 是两回事：

| 指标 | 低了怎么办 |
|---|---|
| judged@10 | 补标注 |
| Recall 上限 | 换主指标（nDCG@10 的理想序已按 K 截断）或接受天花板 |

混在一起看，会把一份标注齐全的报表继续当成「标注没做完」。

## 意图与语种拆分

gold 同时覆盖 en/zh、文本/图像意图。只看 overall 会掩盖：

- 改写在中文上大涨、英文上稀释  
- 视觉列只在图像子集有意义  

读 README 时认准分表；手册不抄数。

## 如何维护 gold

1. `dump_sections` 对照写键  
2. 跑 eval，确认无 dangling  
3. 扩样本后重读小样本时代的「巨大提升」结论（常缩一个数量级）  
4. 改解析导致 section 改名 → gold 必须同步，否则整次评测红灯（这是特性）
