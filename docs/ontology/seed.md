# 种子与 BuiltConcept

源码：`src/biomed_ontology/ingest/seed.py`。

## 为什么存在种子层

外部本体（MONDO / HGNC / ChEMBL…）买得到「公共世界里谁是谁」。买不到的是：

- **概念范围**：阿斯利华管线关心哪些药 / 靶点 / 适应症  
- **企业别名**：内部代号、中文商品名、历史写法  
- **跨类型断言**：药→靶点、药→适应症（在公共源里往往分散或缺失）  

种子 YAML 只承载这些；外部 ID 以 `xref_hints` 提示，由 loader 从**真实快照**解析 —— 手抄 DrugBank ID 无法与快照版本对齐。

## 从 SeedConcept 到 BuiltConcept

```text
data/seed/*.yaml  ──load_seed_file──►  SeedFile / SeedConcept
                              │
                     build_from_seed
                              │
                              ▼
              BuiltConcept + BuiltSynonym + warnings
```

关键字段：

| 字段 | 含义 |
|---|---|
| `key` | 种子内稳定键（如 `savolitinib`），分配内部 CURIE 的输入 |
| `preferred_label_en/zh` | 首选标签 |
| `aliases[]` | 带 `lang` / `scope` / `type` / `source` |
| `parents[]` | 层级（种子键或已解析 id） |
| `targets[]` / `indications[]` | 跨类型链接（经 `LINK_PREDICATES` 变成谓词） |

`LINK_PREDICATES`：

```text
targets     → (has_target, targeted_by)
indications → (treats, treated_by)
```

谓词名与事实层抽取对齐，于是 SPARQL 里同一谓词、靠命名图区分「人工种子断言」与「正文抽取」。

## 构建期必须做的三件事

### 1. 内部 CURIE 分配（D1）

`IdLedger` 把 `seed_key` → `HMDCxxxx`。外部 ID **绝不**当主键。停订 DrugBank 只失去一组 xref，历史报告里的内部 ID 仍有效。

### 2. 别名归一与变体展开（D2）

`normalize_alias` + `generate_code_variants`：让索引侧 `AZD-6094` / `AZD6094` 都能命中。注意：变体是给**索引**用的；查询改写侧必须按 `normalize_alias` **去重**，否则 BM25 会给同一代号投三票（见 [查询改写](../retrieval/ontology-paths.md)）。

### 3. 跨概念 alias_norm 碰撞检测

人工歧义表（`ambiguity.yaml`）总会漏。构建时扫描「同一 `alias_norm` 指向多个概念」→ `unregistered_collisions`。未登记的碰撞进 `kb.warnings`，而不是静默让词典后写覆盖先写。

## ConceptLink 长什么样

```text
ConceptLink(predicate="has_target", object_id="HMDC…", object_key="met")
```

只存**正向**；反向在 `GraphDbNeighborhood` 邻接查询时合成。种子作者不必写 `targeted_by` 边 —— 写了反而会双倍。

## 未解析怎么办

| 警告 | 含义 | 后果 |
|---|---|---|
| `unresolved_parents` | parent 键不在本批概念里 | 层级断边，`expand` / broader 失效 |
| `unresolved_links` | target/indication 键未解析 | search-around 少边，Q4 类查询走不通 |
| `unregistered_collisions` | 同别名多义未登记 | 归一化可能随机落到一个义项 |

构建**不会**因警告失败（PoC 要能先跑起来），但 `hmd kb` 与评测前应扫一眼。生产发版应把「零未解析链接」当成守门。

## 与 registry 的关系

种子伪源 `SEED_INTERNAL` 不在采购 registry 里；tier 在装图时显式定为 `TIER_0`。真实文献源的 tier 来自 `sources.yaml`，跟文档走。

## 如何验证

```bash
uv run pytest tests/test_seed_build.py -q
uv run hmd kb   # 看 concepts / warnings
```

改种子后：先看 warnings，再跑 `hmd eval` 相关臂 —— 尤其是带跨类型意图的 gold（如 VEGFR2 → 药）。
