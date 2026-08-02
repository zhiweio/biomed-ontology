# Tier 策略矩阵

源码：`src/biomed_ontology/licensing.py`（设计决策 D10）。

## 为什么 tier 不是标签

tier 在四个执行点上强制约束：

1. RDF named graph 隔离  
2. 查询可见性过滤（检索 + SPARQL）  
3. 导出闸门（`assert_exportable`）  
4. 训练语料准入（`is_trainable`）  

策略集中在此模块，避免各处散落 `if tier == 2`。许可规则变更时只改一处。

## 策略表

| Tier | 导出 | 训练 | 署名 | Share-alike | 需凭据 | 典型源 |
|---|---|---|---|---|---|---|
| 0 | ✓ | ✓ | | | | MONDO / HGNC / UNII / Wikidata |
| 1 | ✓ | ✗ | ✓ | ✓ | | ChEMBL CC-BY-SA、DrugCentral |
| 2 | ✗ | ✗ | ✓ | | ✓ | UMLS 受限、DrugBank |
| 3 | ✗ | ✗ | ✓ | | ✓ | MedDRA、商业情报原始记录 |

`LicenseViolation` = P0 合规事件，**不做降级处理**（不把 TIER_3 偷偷当成 TIER_1 返回）。

## 候选生成期过滤 vs 返回前裁剪

错误做法：先检出 100 条再丢掉无权的 40 条，对外说「命中 60」。  
正确做法：`LicenseScope.permits` 在 Local/Milvus/图通道候选生成时就生效，无权行根本不进结果集，统计量也不泄漏其存在。

## 命名图

见 [GraphStore](../ontology/rdf.md)。URI 含 tier，便于 `FROM NAMED` 集合级过滤。

## 与数据源 registry

`registry/sources.yaml` 为每个源声明 license / tier / 是否启用。种子伪源 `SEED_INTERNAL` 另议。

## 如何验证

```bash
uv run pytest tests/test_licensing.py tests/test_milvus_license.py -q
```
