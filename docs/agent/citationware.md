# 证据树与 restore_context

源码：`src/biomed_ontology/agentapi/citation.py`（设计决策 D6）。

## 为什么存在

检索返回的是高匹配度**碎片**。碎片能证明「有这句话」，证明不了「在什么语境下说的」。临床结论的语境（哪一组、哪个终点、哪次随访）恰恰决定它成不成立。

Citationware = 从碎片走回原文：拼整节、给面包屑、报原始页码，并在同一许可谓词下执行。

## `restore_context`

```text
chunk_id + scope(SECTION|…) + max_chars + permits(?)
    → RestoredContext(
        doc_id, section_id, breadcrumb, full_text,
        page_start/end, sibling_paths, truncated, license_tier, …
      )
```

| 规则 | 原因 |
|---|---|
| `permits` 由调用方注入并复用 `LicenseScope.permits` | 两套判断 →「检索不可见、还原可见」 |
| `max_chars` 超出置 `truncated=True` | 绝不静默丢内容 |
| 未知 `chunk_id` → KeyError | 不假装还原成功 |

## 证据树 `build_evidence_tree`

把多次检索命中整理成可展示的树（文档 → 节 → 碎片），供 Agent 在回答里挂引用，而不是扔一堆无序 `chunk_id`。

Provenance 是返回体**一等公民**（schema 强制），不是可选 debug 字段。

## 与包装纸过滤的关系

语料切片已尽量去掉页眉页脚等包装纸；还原时仍可能拼回邻近噪声。Citationware 不负责二次清洗正文 —— 清洗在 [chunks](../parse/chunks.md)；还原负责**忠实**给出库内文本。

## 如何验证

```bash
uv run pytest tests/test_agentapi.py -k restore -q
uv run hmd demo   # 含引用/还原断言的场景
```
