# 企业身份与目录 SSOT

源码：`src/biomed_ontology/ingest/seed.py`、`src/biomed_ontology/pipeline.py`（`catalog_files`、`build_literature_base`）。

本文档描述**文献/检索面的企业身份与术语目录**——不是 Foundation 金路径实体的唯一文档（后者见 `ontology/entities/`），但与之共享 `HMD:ENT:*` 命名规则。

---

## 1. 为什么存在

外部本体（MONDO / HGNC / ChEMBL…）提供公共世界里「谁是谁」。企业研发还需要：

- **概念范围**：管线关心的药 / 靶点 / 适应症子集
- **企业别名**：内部代号、中文商品名、历史写法
- **跨类型断言**：药→靶点、药→适应症（公共源分散或缺失）
- **稳定主键**：停订外部源后，历史报告与索引仍有效

这些由 **Ontology-as-Code 目录**承载，构建为 `BuiltConcept` + `BuiltSynonym`，供 `Normalizer`、图通道与 Milvus 索引消费。

---

## 2. 设计取舍

| 决策 | 理由 | 放弃 |
|---|---|---|
| **唯一 SSOT `ontology/catalog/`** | 与金路径实体同仓策展、可 PR 审查 | 双源 / `data/seed` |
| **确定性 `HMD:ENT:*`**（`enterprise_id_for`） | 同 seed_key 永远同一 ID，无需 IdLedger | `HMD:SUB` 递增铸造 |
| **`id_mode=enterprise` 默认** | 生产/文献/评测一致 | 每环境重新 mint |
| **外部 ID 仅 xref_hints** | 与 registry 快照版本对齐 | 手抄 DrugBank ID |

> `data/seed/` 已删除。`id_mode=ledger` 单测请用临时 fixture 目录；新概念写入 `ontology/catalog/` 或金路径 `ontology/entities/`。

---

## 3. 设计与实现

### 3.1 目录解析

```text
pipeline.catalog_files()
    → ontology/catalog/*.yaml（排除 ambiguity.yaml）
    → 目录缺失或空 → FileNotFoundError（硬失败，无回落）
```

常量：

| 符号 | 路径 |
|---|---|
| `ONTOLOGY_CATALOG` | `ontology/catalog/` |
| `catalog` 文件 | `substances.yaml`、`targets.yaml`、`diseases.yaml` 等 |
| 歧义表 | `ontology/catalog/ambiguity.yaml` |

### 3.2 从 SeedConcept 到 BuiltConcept

```text
ontology/catalog/*.yaml
    │ load_seed_file()
    ▼
SeedFile / SeedConcept
    │ build_from_seed(id_mode=enterprise)
    ▼
BuiltConcept + BuiltSynonym + warnings
```

**SeedConcept 关键字段：**

| 字段 | 含义 |
|---|---|
| `key` | 种子内稳定键（如 `savolitinib`），ENT 段 slug 来源 |
| `preferred_label_en/zh` | 首选标签 |
| `aliases[]` | `lang` / `scope` / `type` / `source` |
| `parents[]` | 层级（种子键或已解析 id） |
| `targets[]` / `indications[]` | 跨类型链接 |

**BuiltConcept 关键字段：**

| 字段 | 含义 |
|---|---|
| `concept_id` | `HMD:ENT:{DC\|TGT\|DIS\|…}:{key}` |
| `seed_key` | 原始 key |
| `links` | `ConceptLink` 列表（正向谓词） |
| `license_tier` | 别名来源最高 tier |

### 3.3 企业 ID 分配

`ingest/seed.py` 的 `enterprise_id_for(entity_type, seed_key)`：

```text
HMD:ENT:{segment}:{seed_key}

segment 映射示例：
  DRUG/substance → DC
  TARGET         → TGT
  DISEASE        → IND
```

- `_ENT_OVERRIDES`：少数金路径实体显式覆盖
- `id_mode=ledger`：旧 `IdLedger.mint` → `HMD:SUB|TGT|DIS`（**仅单测**）

### 3.4 类型化链接谓词

种子 YAML 字段 → 运行时谓词（`LINK_PREDICATES`）：

```text
targets     → (has_target, targeted_by)
indications → (treats, treated_by)
```

只存**正向**；反向在 `GraphDbNeighborhood` 邻接查询时合成。谓词与事实层抽取对齐，靠命名图 URI 区分「目录断言」与「正文抽取」。

装载：

- 术语节点：`source_id=SEED_INTERNAL`（`_CATALOG_SOURCE`）
- 类型化链接：`source_id=SEED_LINKS`（`_CATALOG_LINKS_SOURCE`）

### 3.5 构建期三件必做事

**1. 别名归一与变体展开（D2）**

`normalize_alias` + `generate_code_variants`：索引侧 `AZD-6094` / `AZD6094` 均可命中。查询改写侧须按 `normalize_alias` **去重**（见 [查询改写](../retrieval/ontology-paths.md)）。

**2. alias_norm 碰撞检测**

人工歧义表（`ambiguity.yaml`）总会漏。构建扫描「同一 `alias_norm` → 多 concept_id」→ `unregistered_collisions`，进入 `kb.warnings`。

**3. 父节点与链接解析**

| 警告字段 | 含义 |
|---|---|
| `unresolved_parents` | parent 键不在本批概念里 |
| `unresolved_links` | target/indication 端点未解析 |
| `unregistered_collisions` | 未登记歧义 |

构建**不因警告失败**（PoC 先跑通），但 `hmd kb` 与发版前应清零未解析链接。

### 3.6 与 Foundation ER 的关系

| 面 | 身份来源 | 解析入口 |
|---|---|---|
| 文献/检索 | `BuiltConcept.concept_id`（`HMD:ENT:*`） | `normalize.Normalizer` |
| Foundation WM | `ontology/entities/` 策展实体 | `foundation.resolve.EntityResolver` |

两者 ID 规则一致（`HMD:ENT:*`），但 ER 链含 BERN2 / Zingg / 词典，目录构建走 `build_from_seed`。金路径实体以 `ontology/entities/enterprise_entities.yaml` 为准，经 `hmd foundation sync` 入 GraphDB。

### 3.7 调用链（文献装配）

```text
build_literature_base()
    → catalog_files()
    → build_from_seed(..., id_mode="enterprise")
    → Normalizer(concepts, synonyms, ambiguity_index)
    → KnowledgeBase(warnings=...)
```

含 GRAPH 通道时：

```text
ensure_catalog_graphs(graph, concepts, synonyms)
    → GraphStore.load_concepts(SEED_INTERNAL)
    → GraphStore.load_concept_links(SEED_LINKS)
```

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| 运行时权威是 catalog + ENT | 不得再铸造 `HMD:SUB` 作主键 |
| 外部 ID 不当主键 | xref 从快照解析，手抄不可版本化 |
| BROAD 不进精确归一 | scope 必填（D2） |
| 链接只写正向 | 反向查询侧合成 |
| warnings 必须可见 | 静默丢边 → Q4 召不回 |
| seed 伪源 tier | `SEED_INTERNAL` 构建时 `TIER_0` |

| 失败模式 | 表现 |
|---|---|
| 绕过 `ontology/catalog/` 另建术语源 | 与 Foundation 实体分裂 |
| 未解析 `targets:met` | search-around 少边 |
| 未登记歧义碰撞 | 归一化随机落义项 |
| catalog 与 entities 不同步 | resolve 与 normalize 命中不同 ID |

---

## 5. 如何验证

```bash
uv run pytest tests/test_seed_build.py -q
uv run hmd kb                    # concepts 计数 + warnings
uv run pytest tests/test_walk_neighbors.py -q
uv run hmd eval --entitlements MOCK_LICENSED --compact
```

改目录后：先看 `hmd kb` warnings，再跑含跨类型意图的 eval gold（如 VEGFR2 → 药）。

相关：[Pipeline](../architecture/pipeline.md)、[归一化](normalize.md)、[事实抽取](extract.md)、[链接与 search-around](links.md)、[RDF 命名图](rdf.md)。
