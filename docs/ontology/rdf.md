# GraphStore 与许可命名图

源码：`src/biomed_ontology/ontology/rdf.py`（设计决策 D10 / D11）。

## 为什么要 RDF，而不只是 Python 对象

`BuiltConcept` 够跑检索；还要 RDF 是因为：

1. **许可隔离要有物理边界** —— 命名图 URI 编进 tier，SPARQL `FROM NAMED` 可裁剪可见世界  
2. **GraphStore 内部查询**按 entitlement 注入可见命名图（**无**对外公开的 `sparql_query` 工具）  
3. **种子链接 vs 事实边** 谓词同名、证据不同，靠图 URI 区分，而不是两套词汇表  

运行时后端为 **GraphDB**（`GraphDbClient`，与 Foundation 共用 `hmd` 仓库）。  
默认依赖含 `rdflib` / `pyshacl`（SHACL 与导出辅助），不嵌入式三元组库。

## 命名图 URI 约定

`licensing.named_graph_uri(source_id, tier)`：

```text
https://w3id.org/asliva/biomed-ontology/graph/{tier}/{source_id}
```

把 tier 放进 URI 而不是只做属性：SPARQL 侧可以用图名过滤，无需先查属性再过滤 —— 后者容易写成「先查出再丢掉」，统计量会泄漏无权数据的存在性。

KB 许可图与 Foundation 固定图（`graph/ontology|knowledge|provenance|…`）**并存**；KB 装载只 `CLEAR` 自己 register 的命名图。

## 装载的三批图

| 调用 | `source_id` | 内容 |
|---|---|---|
| `load_concepts` | `SEED_INTERNAL` | 概念、标签、同义词、层级 |
| `load_concept_links` | `SEED_LINKS` | `has_target` / `treats` 等种子断言 |
| 事实装载 | 文档源 | 抽取事实 + RDF 1.1 reifier / 出处 |

事实溯源使用标准 RDF 1.1 reification（`rdf:subject` / `rdf:predicate` / `rdf:object`），兼容 GraphDB / RDF4J。

`build_literature_base(with_graph=True)` 将上述投影同步进 GraphDB（需 `task foundation:up`）。默认 `with_graph=False`：术语与检索不依赖灌库。

## 查询时的可见性

GraphStore 内部查询路径根据调用方 `entitlements` 注入允许的命名图集合。模板在 `SPARQL_TEMPLATES`；**不对外暴露** `sparql_query` 工具，也不允许任意用户 SPARQL —— 任意查询是数据面攻击面，也是许可旁路。

## 与检索图通道的关系

| | RDF GraphStore | LinkIndex |
|---|---|---|
| 用途 | 可审计的知识图查询、导出 | 检索期毫秒级 search-around |
| 结构 | GraphDB 三元组 + 命名图 | 内存邻接表 |
| 许可 | FROM NAMED | `_graph_allowed` + `LicenseScope` |

两端必须表达**同一套**业务边；但实现上不共享一个存储 —— 检索热路径不能每次 SPARQL。改种子链接时：同时影响 `LinkIndex`（经 `BuiltConcept.links`）与 RDF 装载。

## 如何验证

```bash
# 默认 CI（respx / 注入 client，无 Docker）
uv run pytest tests/test_rdf.py tests/test_graphstore_graphdb.py -q

# 真实 GraphDB
task foundation:up
uv run pytest -m integration
```

重点：无权 entitlement 下，受限图既不能出现在 SPARQL 结果里，也不能通过「计数类」查询被感知。
