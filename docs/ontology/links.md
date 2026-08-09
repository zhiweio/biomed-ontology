# 类型化链接与 search-around

源码：`src/biomed_ontology/ontology/links.py`（`walk_neighbors`）+
`src/biomed_ontology/ontology/neighborhood.py`（`GraphDbNeighborhood`）。

这是本体在检索里**区别于同义词表**的地方。读懂这一章，才能理解为什么「有本体」不等于「ontology_hybrid 一定涨分」。

## 问题：层级解决不了的查询

层级扩展（`skos:broader` / `narrower`）只能在**同类实体**内部走：

- 查「肺癌」→ 能带出「肺腺癌」  
- 查「VEGFR2 抑制剂」→ 归一到靶点 KDR 后**无处可去** —— 靶点没有「下位药」  

真实问题几乎都是跨类型的：靶点→药、药→适应症。种子里写了 `targets` / `indications`，若查询期不走这些边，图通道只是换皮的同义词倒排。

## 边权威：GraphDB

生产路径的边存 GraphDB 命名图（`SEED_INTERNAL` / `SEED_LINKS`），经
`ensure_catalog_graphs` 灌入。`GraphDbNeighborhood.adjacency_many` 一次 SPARQL
取 frontier 的出/入边（层级 + 类型化，反向在查询侧合成），进程内跑
`walk_neighbors`（BFS 策略：**不**下沉到 SPARQL）。

无内存 `LinkIndex`；oaklib 不承担企业 `HMD:ENT:*` search-around。

## 三条设计约束

### 1. 反向边与正向边同等重要

种子只写药→靶点，但「MET 抑制剂有哪些」问的是反过来。邻接侧合成反向谓词：

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

### 3. 一条路径上最多一次跨类型跳

层级可传递复合。跨类型不行：`has_target ∘ targeted_by` = 竞品关系。
BFS 状态是 `(概念, 是否已跨类型)`。

## `walk_neighbors()` 算法要点

```text
frontier: (concept_id, crossed) → carried_weight
每跳:
  adjacency(frontier)  # GraphDB SPARQL 一批
  for 边 (dst, predicate):
    typed = predicate ∉ {broader, narrower}
    if typed and crossed: skip
    weight = carried * RELATION_DECAY[predicate]
    if weight < min_weight: skip
    保留到达 dst 的最高权重路径
```

## 与 `Normalizer._children` 的分工

| | `_children` | `GraphDbNeighborhood` |
|---|---|---|
| 边 | 仅层级 | 层级 + 类型化链接 |
| 方向 | 向下 | 双向（查询侧合成） |
| 用途 | `expand` / 别名 | 检索期 search-around |
| 合并？ | **不要** | 别名扩展沿层级；召回沿全部关系 |

## 图通道如何消费它

`HybridSearcher._graph_channel`：

1. 查询 → seed 概念（`normalize`）  
2. `neighborhood.neighbors(seeds, max_hops=2)` → 查询侧概念向量  
3. 对每个概念，累加 `qw * idf²` 到挂载该概念的 chunk  
4. 除以文档侧概念向量模长  
5. 排序进 RRF（通道权重 0.5）  

词法/稠密通道走 Milvus（`sparse_lexical` + dense_*），不经 Local。

详见 [查询改写 vs 图通道](../retrieval/ontology-paths.md) 与 [带权 RRF](../retrieval/hybrid.md)。

## 如何验证

```bash
uv run pytest tests/test_walk_neighbors.py tests/test_tools.py -k "search_around or walk" -q
```
