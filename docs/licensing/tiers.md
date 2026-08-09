# Tier 策略矩阵

源码：`src/biomed_ontology/licensing.py`（设计决策 D10）。  
数据登记：`data/registry/sources.yaml`；图隔离见 [RDF 命名图](../ontology/rdf.md)。

## 为什么存在

生物医学数据源许可差异极大：MONDO 可开放复用，ChEMBL 要求署名与 share-alike，UMLS / DrugBank / MedDRA 需订阅凭据。若 tier 只是文档标签、执行时各处手写 `if tier == 2`，必然出现：

- 检索「先检出再裁剪」，对外谎称命中数；
- 导出物夹带不可分发内容；
- 训练语料悄悄混入受限源；
- SPARQL / 命名图无法做集合级过滤。

Tier 因此是**四个执行点上的强制约束**，策略集中在 `POLICIES` 单模块，规则变更只改一处。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 策略表达 | `TierPolicy` 结构化字段（export / train / attribution / share_alike / entitlement） | 散落魔法数字 |
| 越界处理 | `LicenseViolation` = P0，**不降级** | 把 TIER_3 悄悄当 TIER_1 返回 |
| 过滤时机 | 候选生成期 `LicenseScope.permits` | 返回前丢掉无权行并改 total |
| 图隔离 | tier 编入 named graph URI | 仅三元组属性过滤 |
| 无凭据默认可见性 | 最高 TIER_1（内部可读，分发受限） | 默认全开或默认全关 |
| 凭据粒度 | 源 ID 集合（如 `UMLS`、`MEDDRA`） | 按文档逐条 ACL |

## 设计与实现

### 四执行点

```text
1. RDF named graph 隔离     → named_graph_uri(source, tier)
2. 查询可见性过滤           → LicenseScope.permits + max_visible_tier(entitlements)
3. 导出闸门                 → assert_exportable(tier)
4. 训练语料准入             → is_trainable(tier)
```

### 策略表（`POLICIES`）

| Tier | 导出 | 训练 | 署名 | Share-alike | 需凭据 | 典型源 |
|---|---|---|---|---|---|---|
| 0 | ✓ | ✓ | | | | MONDO / HGNC / UNII / Wikidata |
| 1 | ✓ | ✗ | ✓ | ✓ | | ChEMBL CC-BY-SA、DrugCentral |
| 2 | ✗ | ✗ | ✓ | | ✓ | UMLS 受限、DrugBank |
| 3 | ✗ | ✗ | ✓ | | ✓ | MedDRA、商业情报原始记录 |

`tier_rank()` 提供全序比较；`max_visible_tier(entitlements)` 在无凭据时返回 TIER_1，持源 ID 凭据时可至 TIER_3。

### 候选生成期过滤 vs 返回前裁剪

```text
错误：检出 100 条 → 丢掉 40 条无权 → 对外说「命中 60」
正确：permits 在 Local / Milvus / 图通道生成候选时生效 → 无权行不进结果集，统计也不泄漏其存在
```

`LicenseScope` 还用于 `restore_context`（见 [citationware](../tools/citationware.md)），保证还原不能成为越权后门。

### 命名图

`named_graph_uri(source_id, tier)` → `https://w3id.org/asliva/biomed-ontology/graph/{tier}/{source}`

tier 放进 URI 而非只做属性，是为让 SPARQL `FROM NAMED` 做**集合级**过滤。

### 与数据源 registry

`data/registry/sources.yaml` 为每个源声明 `license` / `tier` / 是否启用。种子伪源 `SEED_INTERNAL` 另议（内部演示，不冒充外部权威）。

### 与组件闸门的分工

本文件管**数据** tier；第三方解析器 / 模型许可见 [components](components.md)。两者不得混进同一张执行表。

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| `LicenseViolation` 不捕获降级 | 合规 P0；审计可追溯 |
| 过滤在候选期 | 命中数与存在性泄漏 |
| 导出前 `assert_exportable` | 受限内容离开系统边界 |
| `restore_context` 与检索同一 `permits` | 碎片 id 换全文后门 |
| eval 需 `MOCK_LICENSED` 等凭据 | 无凭据时 Bridge 许可用例失败（预期） |

失败模式：

- **误把 tier 当排序特征**：tier 不参与相关性，只参与可见性。
- **registry 与图 URI 不一致**：SPARQL 过滤漏图。
- **评测与生产凭据不一致**：数字不可横向比较。

## 如何验证

```bash
uv run pytest tests/test_licensing.py tests/test_milvus_license.py -q
uv run hmd eval --entitlements MOCK_LICENSED   # Bridge 许可还原用例
```

组件闸门见 [components](components.md)；NOTICE 义务见 [notice](../appendix/notice.md)。
