# GraphStore 与许可命名图

源码：`src/biomed_ontology/ontology/rdf.py`（设计决策 D10 / D11）。

## 为什么要 RDF，而不只是 Python 对象

`BuiltConcept` 够跑检索；还要 RDF 是因为：

1. **许可隔离要有物理边界** —— 命名图 URI 编进 tier，SPARQL `FROM NAMED` 可裁剪可见世界  
2. **Agent 的 `sparql_query`** 走受控模板，自动注入可见图  
3. **种子链接 vs 事实边** 谓词同名、证据不同，靠图 URI 区分，而不是两套词汇表  

运行时用 `pyoxigraph`（`extra rdf`）。

## 命名图 URI 约定

`licensing.named_graph_uri(source_id, tier)`：

```text
https://w3id.org/asliva/biomed-ontology/graph/{source_id}/tier-{n}
```

把 tier 放进 URI 而不是只做属性：SPARQL 侧可以用图名过滤，无需先查属性再过滤 —— 后者容易写成「先查出再丢掉」，统计量会泄漏无权数据的存在性。

## 装载的三批图

| 调用 | `source_id` | 内容 |
|---|---|---|
| `load_concepts` | `SEED_INTERNAL` | 概念、标签、同义词、层级 |
| `load_concept_links` | `SEED_LINKS` | `has_target` / `treats` 等种子断言 |
| 事实装载 | 文档源 | 抽取事实 + reifier / 出处 |

## 查询时的可见性

`sparql_query` / GraphStore 查询路径根据调用方 `entitlements` + `max_tier` 注入允许的命名图集合。模板在 `SPARQL_TEMPLATES`；**不允许**任意用户 SPARQL —— 任意查询是数据面攻击面，也是许可旁路。

## 与检索图通道的关系

| | RDF GraphStore | LinkIndex |
|---|---|---|
| 用途 | 可审计的知识图查询、导出 | 检索期毫秒级 search-around |
| 结构 | 三元组 + 命名图 | 内存邻接表 |
| 许可 | FROM NAMED | `_graph_allowed` + `LicenseScope` |

两端必须表达**同一套**业务边；但实现上不共享一个存储 —— 检索热路径不能每次 SPARQL。改种子链接时：同时影响 `LinkIndex`（经 `BuiltConcept.links`）与 RDF 装载。

## 如何验证

```bash
uv run pytest tests/test_rdf.py tests/test_milvus_license.py -q
```

重点：无权 entitlement 下，受限图既不能出现在 SPARQL 结果里，也不能通过「计数类」查询被感知。
