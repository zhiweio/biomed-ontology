# GraphStore 与许可命名图

源码：`src/biomed_ontology/ontology/rdf.py`（`GraphStore`、`GraphDbClient` 封装）。

设计决策 D10 / D11：许可隔离要有物理边界；种子链接与事实边谓词同名、证据不同，靠命名图 URI 区分。

---

## 1. 为什么存在

`BuiltConcept` 与 Python 字典够跑内存检索，但还需要 RDF 层因为：

1. **许可隔离** — 命名图 URI 编入 tier，查询按图裁剪可见世界
2. **GraphDB 内部查询** — 按 entitlement 注入 `FROM NAMED`，无对外任意 SPARQL
3. **种子链接 vs 事实边** — 同一谓词、不同证据强度，分图存储
4. **Foundation 共存** — KB 许可图与 `graph/ontology|knowledge|provenance` 同一 Repository

运行时后端为 **GraphDB**（与 Foundation 共用仓库配置）。默认依赖含 `rdflib` / `pyshacl`（SHACL 与导出辅助），不嵌入式三元组库。

---

## 2. 设计取舍

| 决策 | 理由 |
|---|---|
| tier 编进命名图 URI | SPARQL 用图名过滤，避免「先查属性再丢行」泄漏存在性 |
| `SEED_INTERNAL` / `SEED_LINKS` 分图 | 术语 vs 类型化断言合规分流 |
| 事实 RDF 1.1 reification | 标准溯源，兼容 GraphDB / RDF4J |
| 不对外 `sparql_query` 工具 | 任意 SPARQL = 许可旁路 + 攻击面 |
| 默认 `with_graph=False` 构建 KB | 归一化不强制 GraphDB；GRAPH 臂按需 ensure |
| KB 装载只 CLEAR 自己注册的图 | 不碰 Foundation 固定图与 extracted 图 |

---

## 3. 设计与实现

### 3.1 命名图 URI 约定

`licensing.named_graph_uri(source_id, tier)`：

```text
https://w3id.org/asliva/biomed-ontology/graph/{tier}/{source_id}
```

把 tier 放进 URI 而不是只做属性：SPARQL 侧用图名过滤，无需先查 tier 属性再过滤——后者易写成「先查出再丢掉」，统计量会泄漏无权数据的存在性。

### 3.2 文献 KB 三批装载

| 调用 | `source_id` | 内容 |
|---|---|---|
| `load_concepts` | `SEED_INTERNAL` | 概念、skos 标签、同义词、层级 |
| `load_concept_links` | `SEED_LINKS` | `has_target` / `treats` 等目录断言 |
| `load_corpus` + `load_facts` | 文档 `source_id` | 语料切片 + 抽取事实 + reifier |

`pipeline.ensure_catalog_graphs` 封装前两批（GRAPH 通道前置）：

```text
ensure_catalog_graphs(graph, concepts, synonyms)
    → load_concepts(..., SEED_INTERNAL, TIER_0)
    → load_concept_links(..., SEED_LINKS, TIER_0)
```

`build_literature_base(with_graph=True)` 在构建期执行相同装载，并额外按 `source_id` 分区装载语料与事实。

### 3.3 Foundation 固定图（并存）

| 图 URI | 内容 | 写入方 |
|---|---|---|
| `graph/biomedical` | BIOS_v3 | `hmd foundation bios-load` |
| `graph/ontology` | Enterprise 实体 TBox/ABox | `foundation sync` |
| `graph/knowledge` | validated 关系边 | `foundation sync` |
| `graph/provenance` | seed 策展 claim | `foundation sync` |
| `graph/provenance_extracted` | 湖侧 extracted claim | `lake ingest` |

`foundation/sync.py` 的 `sync_world_model` **保留** `GRAPH_PROVENANCE_EXTRACTED`，仅 replace ontology/knowledge/provenance seed 图。

### 3.4 `GraphStore` 查询路径

- 内部 SPARQL 模板：`SPARQL_TEMPLATES`（`ontology/rdf.py`）
- 调用方传入 `entitlements: frozenset[str]` → 注入允许命名的 `FROM NAMED` 集合
- `GraphDbNeighborhood.adjacency_many` 使用 `unrestricted=True` 的邻接查询（仍受 Repository 配置约束；生产应配合 entitlement）

**不**对外暴露 `sparql_query` Semantic 工具。

### 3.5 CURIE ↔ IRI

| 函数 | 路径 | 用途 |
|---|---|---|
| `curie_to_iri` | `ontology/rdf.py` | 装载与 SPARQL |
| `iri_to_curie` | `ontology/neighborhood.py` | 邻接结果还原 |

企业 ID 示例：`HMD:ENT:DC:savolitinib` → `https://w3id.org/asliva/.../HMD_ENT_DC_savolitinib`（HMD 本地段规则见 `neighborhood.iri_to_curie` 注释）。

### 3.6 与检索图通道的关系

| | RDF GraphStore | 检索 search-around |
|---|---|---|
| 用途 | 可审计知识图、导出、邻接边权威 | 图通道打分 |
| 结构 | GraphDB 三元组 + 命名图 | SPARQL 一跳出/入 + `walk_neighbors` |
| 许可 | FROM NAMED + entitlement | `_graph_allowed` + `LicenseScope` |
| 策略 | 存边 | 衰减、跨类型一跳、`min_weight` 在进程内 |

改目录链接：经 `ensure_catalog_graphs` / `load_concept_links` 影响 GraphDB 边；`HybridSearcher` 索引期 `concept_ids` 倒排需重新 `hmd kb` + `hmd index`。

### 3.7 SHACL 与导出

- `quality/` + `pyshacl`：入图前验证（`ontology:validate` 链路）
- `rdflib`：TTL 解析/序列化辅助，非运行时主存储

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| 无权图不出现在结果 | entitlement 注入 |
| 计数查询也不泄漏 | 禁止先全量再过滤 |
| 链接与术语分图 | `SEED_LINKS` ≠ `SEED_INTERNAL` |
| sync 不清 extracted | 湖侧 claim 独立生命周期 |
| GRAPH 臂 ensure 失败即报错 | 禁止假装跑图通道 |
| 事实用 reification | 出处可还原 |

| 失败模式 | 表现 |
|---|---|
| GraphDB 不可达 | `ensure_catalog_graphs` RuntimeError |
| 混装链接进术语图 | SPARQL/导出无法按断言强度分流 |
| 手改 GraphDB 不回写 catalog | 目录与图漂移 |
| Free 版并发限制 | 多 Agent 查询排队 |

---

## 5. 如何验证

```bash
# 默认 CI（respx / 注入 client，无 Docker）
uv run pytest tests/test_rdf.py tests/test_graphstore_graphdb.py -q

# 真实 GraphDB
task foundation:up
uv run pytest -m integration -q

# 端到端 GRAPH 臂
uv run hmd kb
task foundation:up
uv run hmd index --recreate
uv run hmd eval --entitlements MOCK_LICENSED --compact
```

重点：无权 entitlement 下，受限图既不能出现在 SPARQL 结果里，也不能通过计数类查询被感知。

相关：[links / search-around](links.md)、[Pipeline](../architecture/pipeline.md)、[Foundation](../architecture/foundation.md)、[不变量](../invariants.md)。
