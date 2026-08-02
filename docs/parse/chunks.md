# 切片与包装纸过滤

源码：`src/biomed_ontology/corpus/`（`chunk_document` 等）。

## 切片在流水线中的位置

```text
Document（语料 YAML）
  → chunk_document
  → 每片 normalize → concept_ids
  → 进入 BM25/向量索引 + 概念倒排
```

切片是检索的原子单位，也是 Citationware 还原的锚点（`chunk_id` → section 成员集合）。

## 包装纸过滤：为什么必须做

页眉、页脚、期刊模板句（「All rights reserved」「Corresponding author」）对 BM25 极不友好：高频、跨文档重复，IDF 低却占词频。更糟的是它们有时带药名/适应症词，造成虚假命中。

过滤应在**入库切片**时做，而不是检索期打补丁 —— 否则 Local 与 Milvus、评测与服务会不一致。

## 稳定 chunk_id

跨进程重建知识库时，同一文档同一节的 `chunk_id` 必须稳定。曾经出现「跨进程漂移」导致 Milvus 里的主键对不上当前 KB —— 集成测试长期 skip 时这个问题会藏很久。

改切片算法时：`--recreate` 索引，并确认评测/服务读同一 release。

## 模态字段

`modality`（TEXT / IMAGE / …）与后续 `figure_type` 都挂在 chunk 上，进入 `ChunkMeta` → 后端标量。空 `figure_type` = 未分类，不是「非图」。

## 如何验证

```bash
uv run pytest tests/test_parse.py -q
uv run hmd kb   # chunks 计数与 warnings
```
