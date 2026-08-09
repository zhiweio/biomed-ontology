# 类型化链接与 search-around

源码：

- `src/biomed_ontology/ontology/links.py` — `walk_neighbors`、`RELATION_DECAY`、`Neighbor`
- `src/biomed_ontology/ontology/neighborhood.py` — `GraphDbNeighborhood`、`ConceptNeighborhood`

这是本体在检索里**区别于同义词表**的能力。读懂本章，才能理解为什么「有本体」不等于「ontology_hybrid 一定涨分」。

---

## 1. 为什么存在

### 1.1 层级解决不了的问题

层级扩展（`skos:broader` / `narrower`）只能在**同类实体**内部走：

- 查「肺癌」→ 能带出「肺腺癌」
- 查「VEGFR2 抑制剂」→ 归一到靶点 KDR 后**无处可去**（靶点没有「下位药」）

真实问题几乎都是跨类型的：靶点→药、药→适应症。目录里写了 `targets` / `indications`，若查询期不走这些边，图通道只是换皮的同义词倒排。

### 1.2 边需要运行时权威

生产路径的边存 **GraphDB 命名图**（`SEED_INTERNAL` / `SEED_LINKS`），经 `ensure_catalog_graphs` 灌入。进程内用 `walk_neighbors` 做带权 BFS；**不**把完整 BFS 下沉到 SPARQL，以便衰减策略、跨类型一跳约束可版本化与单测。

无内存 `LinkIndex`；oaklib 不承担企业 `HMD:ENT:*` search-around。

---

## 2. 设计取舍

| 约束 | 理由 |
|---|---|
| 反向边与正向同等重要 | 种子只写药→靶点；「MET 抑制剂有哪些」问反向 |
| 每种关系自己的衰减 | 上位/下位语义距离小；药→靶点距离大 |
| 路径上最多一次跨类型跳 | `has_target ∘ targeted_by` = 竞品关系，不走 |
| 多路径保留最高权重 | 同概念不同路径到达 |
| BFS 在进程内 | SPARQL 只做一跳出/入边批量查询 |

---

## 3. 设计与实现

### 3.1 谓词与衰减

`RELATION_DECAY`（`ontology/links.py`）：

| 谓词 | 衰减 | 直觉 |
|---|---|---|
| `narrower` | 0.8 | 更细粒度同类 |
| `broader` | 0.7 | 更粗粒度同类 |
| `has_target` / `treats` | 0.65 | 跨实体关联 |
| `targeted_by` / `treated_by` | 0.55 | 反向，扇出更大 |

种子字段 → 谓词（`ingest/seed.py` 的 `LINK_PREDICATES`）：

```text
targets     → has_target (+ 邻接合成 targeted_by)
indications → treats (+ 邻接合成 treated_by)
```

### 3.2 `walk_neighbors` 算法

```text
输入: seeds, adjacency(cids)→{src: [(dst, predicate), ...]}, max_hops, min_weight

frontier: (concept_id, crossed) → carried_weight
  crossed = 本路径是否已走过跨类型边

for hop in 1..max_hops:
  edges = adjacency(frontier 中的 concept_id 集合)
  for each (cid, crossed), carried in frontier:
    for (dst, predicate) in edges[cid]:
      if dst in seeds: skip
      typed = predicate ∉ {broader, narrower}
      if typed and crossed: skip          # 最多一次跨类型
      weight = carried * RELATION_DECAY[predicate]
      if weight < min_weight: skip
      保留 dst 的最高 weight 路径
      frontier' += (dst, crossed or typed)

返回: sorted Neighbor(concept_id, hops, predicate, weight)
```

`Neighbor` 的 `weight` 已含沿途衰减乘积。

### 3.3 `GraphDbNeighborhood`

```text
GraphDbNeighborhood.neighbors(seeds, max_hops=2, ...)
    → walk_neighbors(seeds, self.adjacency_many, ...)
```

`adjacency_many(cids)` 流程：

1. CURIE → IRI（`curie_to_iri`）
2. 单条 SPARQL：`VALUES ?s { ... }` + UNION：
   - `skos:broader` / 反向 `narrower`
   - `hmd:has_target` / 反向 `hmd:targeted_by`
   - `hmd:treats` / 反向 `hmd:treated_by`
3. 结果映射回 CURIE（`iri_to_curie`）
4. 返回 `{src_curie: [(dst_curie, predicate), ...]}`

`entitlements` 传入 `GraphStore.query` 控制可见命名图。失败抛 `RuntimeError`（GraphDB 邻接查询失败）。

`NullNeighborhood`：返回空列表，用于不需要 GRAPH 通道的装配（如仅写 Milvus 行）。

### 3.4 与 `Normalizer.expand` 的分工

| | `Normalizer._children` / `expand` | `GraphDbNeighborhood` |
|---|---|---|
| 边 | 仅层级 | 层级 + 类型化 |
| 方向 | 向下 | 双向（查询合成） |
| 用途 | BM25/DENSE 查询改写 | 图通道 search-around |
| 合并？ | **禁止** | — |

### 3.5 图通道消费（`HybridSearcher._graph_channel`）

```text
1. seeds = normalize(query, min_confidence=0.6)   # 或调用方传入
2. query_vec: seeds 权重 1.0
3. if expand: neighbors = neighborhood.neighbors(seeds, max_hops=2)
              合并邻居权重到 query_vec
4. 对每个概念 cid, qw in query_vec:
     gain = qw * idf(cid)²
     累加到挂载 cid 的 chunk_id（须在 _graph_allowed 内）
5. score(chunk) /= concept_norm(chunk)            # 文档侧模长归一
6. 排序 → 送入 rrf_fuse（通道权重见 CHANNEL_WEIGHTS）
```

词法/稠密走 Milvus（`sparse_lexical` + dense_*），不经 Local 内存后端。

装配入口：

```text
runtime.build_literature_searcher()
    → ensure_catalog_graphs()
    → HybridSearcher(kb, backend=milvus, neighborhood=GraphDbNeighborhood)
```

### 3.6 IDF 与模长（为何图通道不是「按跳数排序」）

- `_build_concept_idf`：`log(N/df)`，下界 0.1，防止高频概念垄断
- `_build_concept_norms`：切片概念向量模长，专论短段高于挂满概念的综述段

三者（类型化邻接 + IDF + 模长）缺一则候选易大量并列，RRF 次级键退化。

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| 边权威在 GraphDB | 改 catalog 链接需 re-ensure / sync |
| 种子只写正向边 | 反向仅查询侧合成 |
| 跨类型最多一跳 | `walk_neighbors` 的 `crossed` 标志 |
| 图通道同许可滤 | `_graph_allowed` ≡ 后端 scope |
| 与 expand 不合并 | 别名扩展不带竞品 |
| GRAPH 臂需 GraphDB 可达 | `ensure_catalog_graphs` 硬失败 |

| 失败模式 | 表现 |
|---|---|
| `unresolved_links` | 特定跨类型 query 召不回 |
| GraphDB 未灌 | GRAPH 臂空 |
| 省略 IDF | 图通道近似随机 |
| SPARQL 做完整 BFS | 难测衰减策略、难控跨类型约束 |

---

## 5. 如何验证

```bash
uv run pytest tests/test_walk_neighbors.py -q
uv run pytest tests/test_tools.py -k "search_around or walk" -q
uv run pytest tests/test_search_backend.py -q
task foundation:up   # 集成：真实 GraphDB 邻接
uv run pytest -m integration -k graph -q 2>/dev/null || true
```

相关：[查询改写 vs 图通道](../retrieval/ontology-paths.md)、[带权 RRF](../retrieval/hybrid.md)、[RDF 命名图](rdf.md)、[企业身份与目录 SSOT](seed.md)。
