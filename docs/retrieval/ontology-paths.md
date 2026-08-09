# 查询改写 vs 图通道

本体经由**两条互相独立**的路径参与检索。缺一条，「本体增强」这个臂名就不成立。早先只有图通道时，`expand` 开与不开对总分差别可以小到噪声（代码注释里记过 +0.002 量级）—— 因为分数主要来自词法/向量，而那两条完全没吃到本体。

## 两条路径对照

| | 图通道 | 查询改写 |
|---|---|---|
| 开关 | `channels` 含 GRAPH；`expand` 控制是否 search-around | `rewrite`（默认跟随 `expand`） |
| 作用点 | 概念倒排 → chunk 得分 | 改写下发给 BM25 / DENSE 的查询串 |
| 依赖 | GraphDB 邻域 + IDF + 模长 | `Normalizer.expand` + `normalize_alias` |
| 消融臂 | ② 仅种子 / ③ + hops | ④ 仅改写（无图） |
| 典型收益场景 | 跨类型：「VEGFR2 抑制剂」→ 药 | 中文别名、代号 ↔ 通用名 |
| 典型伤害场景 | 链接噪声、IDF 失效时的并列 | 英文原词本已命中时，别名稀释 BM25 |

拆成两个开关的唯一理由：**归因**。一次全开时，涨跌都说不清是哪条路。

## 图通道打分（实现级）

查询侧概念向量 \(q\)：种子权重 1.0，邻居为关系衰减后的 weight。  
文档侧：切片挂载的概念集合，按概念 IDF 加权，模长为 \(\|d\|\)。

对概念 \(c\) 的贡献累加：

\[
\mathrm{gain}(c) = q_c \cdot \mathrm{idf}(c)^2
\]

再 \(\mathrm{score}(d) \leftarrow \mathrm{score}(d) / \|d\|\)。  
这与稠密通道同一数学形式（余弦），向量空间从字符 n-gram 换成了概念图。

IDF 下界 0.1：概念挂满全库时 \(\log(N/df)=0\)，若直接归零会抹掉整条路径，连带稀有邻居一起消失。

## 查询改写的三条约束

`_rewrite_queries` 每条对应一种具体失败：

### 1. 按 `normalize_alias` 去重

别名表里 `AZD-6094` / `AZD 6094` / `AZD6094` 是三行（给**索引侧**任意写法匹配）。原样拼进查询串 → BM25 词频×3。索引侧已归一，查询侧只需一种写法。

### 2. 词法拼接、向量取 max

- **词法**：多一个词多一条命中路径，BM25 自带 IDF 压烂大街扩展词。改写串 = `原查询 + 选中别名`。  
- **向量**：把八个别名拼进一句话 → 质心偏离原意。因此 dense 拿到 `(原串, 改写串)` 两条，各自编码后取最高分 —— **原串始终在集合里**，改写只能加分，不能把语义拽走。

### 3. `max_terms=8` 封顶

gold 里考察「层级扩展是否过度召回」的 query：肺癌子树别名可以灌进几十个。不封顶时原始查询词被稀释到不起作用。

另外会去掉原查询里已有的词 —— 重复只抬词频，不是扩展。

## 消融时如何读

建议顺序（Milvus 臂）：

1. `bm25_dense` —— 无本体基线  
2. `bm25_dense_graph` —— 只加图通道、**不** search-around（`expand=False`）  
3. `bm25_dense_hops` —— +search-around，`rewrite=False`  
4. `bm25_dense_expand` —— 仅改写，无图  
5. `ontology_hybrid` —— 全开  

若 ② 为负而 ③ 转正：旧「哈希并列」类问题或链接开始起作用。  
若 ④ 中文大涨、英文微跌：符合「英文原词本已命中」的机制预期，不要用总平均一刀切。

!!! info "数字在 README"
    具体抬升/显著性只维护在 README；这里只教读法。

## 概念注入索引文本

`hmd index` 经 `chunk_to_row(..., label_terms=…)` 写入 Milvus 的文本是：

```text
chunk.text + " " + preferred_label_en/zh（该片挂载概念）
```

让文中写 ORPATHYS、查询写「沃利替尼」时，`sparse_lexical` 仍有机会命中。这与查询改写互补：一个改索引侧可见字符串，一个改查询侧。

## 如何验证

改 `_rewrite_queries` 或 `_graph_channel` 后：

```bash
uv run hmd eval --entitlements MOCK_LICENSED
# 对照臂 ②③④ 与 by_lang 拆分，不要只看 overall
```
