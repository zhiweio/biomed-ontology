# 类型化链接与 search-around

源码：`src/biomed_ontology/ontology/links.py`。

这是本体在检索里**区别于同义词表**的地方。读懂这一章，才能理解为什么「有本体」不等于「ontology_hybrid 一定涨分」。

## 问题：层级解决不了的查询

层级扩展（`skos:broader` / `narrower`）只能在**同类实体**内部走：

- 查「肺癌」→ 能带出「肺腺癌」  
- 查「VEGFR2 抑制剂」→ 归一到靶点 KDR 后**无处可去** —— 靶点没有「下位药」  

真实问题几乎都是跨类型的：靶点→药、药→适应症。种子里写了 `targets` / `indications`，若查询期不走这些边，图通道只是换皮的同义词倒排。

## 三条设计约束

### 1. 反向边与正向边同等重要

种子只写药→靶点，但「MET 抑制剂有哪些」问的是反过来。`LinkIndex` 建双向邻接，谓词区分方向：

| 正向 | 反向 |
|---|---|
| `has_target` | `targeted_by` |
| `treats` | `treated_by` |
| `narrower` / `broader` | 层级双向 |

衰减可以给两个方向配不同值（反向扇出通常更大 → 权重更低）。

### 2. 每种关系有自己的衰减

`RELATION_DECAY`：

| 谓词 | 衰减 | 直觉 |
|---|---|---|
| `narrower` | 0.8 | 同一东西的更细粒度 |
| `broader` | 0.7 | 同一东西的更粗粒度 |
| `has_target` / `treats` | 0.65 | 两个不同东西相关 |
| `targeted_by` / `treated_by` | 0.55 | 反向，扇出更大 |

用同一个 0.8 会让二跳靶点盖过一跳下位概念 —— 那不是查询意图。

### 3. 一条路径上最多一次跨类型跳

层级可传递复合（孙子仍是祖父的特化）。跨类型不行：

\[
\text{has\_target} \circ \text{targeted\_by}
\]

展开是「共享靶点的另一些药」= **竞品关系**，不是「回答同一个问题」。一篇讲另一种 MET 抑制剂的文章，对「赛沃替尼疗效如何」没有价值，但在图上只有两跳。

实现：BFS 状态是 `(概念, 是否已跨类型)`，不是单纯的概念 id。已经 `crossed=True` 时再遇非层级边 → `continue`。

## `neighbors()` 算法要点

```text
frontier: (concept_id, crossed) → carried_weight
每跳:
  for 边 (dst, predicate):
    typed = predicate ∉ {broader, narrower}
    if typed and crossed: skip
    weight = carried * RELATION_DECAY[predicate]
    if weight < min_weight: skip   # 默认 0.1，防止退化为「全图」
    保留到达 dst 的最高权重路径
```

同一概念多条路径到达 → 只留权重最高的那条（能写进 explain 而不误解）。

`min_weight` 是硬止损：不设的话图上任意两点几乎都有路径，图通道判别力被稀释 —— 这是老毛病。

## 与 `Normalizer._children` 的分工

| | `_children` | `LinkIndex` |
|---|---|---|
| 边 | 仅层级 | 层级 + 类型化链接 |
| 方向 | 向下 | 双向 |
| 用途 | `expand` / 别名 | 检索期 search-around |
| 合并？ | **不要** | 别名扩展沿层级；召回沿全部关系 |

## 图通道如何消费它

`HybridSearcher._graph_channel`：

1. 查询 → seed 概念（`normalize`）  
2. `links.neighbors(seeds, max_hops=2)` → 查询侧概念向量（种子 1.0，邻居带衰减）  
3. 对每个概念，累加 `qw * idf²` 到挂载该概念的 chunk  
4. 除以文档侧概念向量模长（余弦形式）  
5. 排序进 RRF（通道权重 0.5）  

详见 [查询改写 vs 图通道](../retrieval/ontology-paths.md) 与 [带权 RRF](../retrieval/hybrid.md)。

## 事故课（预告）

改造前图通道只有三档分值取 max，几百片并列，次级键是 SHA-1 前缀的 `chunk_id` —— 名义上的「本体增强」其实是哈希抽样。修复需要 **链接 + IDF + 模长** 三处同时改，只改一处不够。

## 如何验证

```bash
uv run pytest tests/ -k "link or graph or hops" -q
# 消融臂 ②→③ 打开 search-around（expand=True, rewrite=False）
uv run hmd eval --entitlements MOCK_LICENSED
```

读数方法见 [ARMS](../eval/arms.md)。数字以 README 为准。
