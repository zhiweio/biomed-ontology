# 切片与 Tree Chunk（Evidence Object）

源码：`src/biomed_ontology/corpus/`（`chunk_document`、`tree.py`）。

## 切片在流水线中的位置

```text
Document（语料 YAML / parse）
  → build_document_tree（Tree Chunk 引擎）
  → tree_to_chunks → Evidence Object 叶节点
  → BERN2 entity_ids + Milvus / Iceberg
```

扁平 `chunk_document` 仍可用于文献检索装配；入湖双写以 **Tree Chunk** 为准。  
Evidence Object 字段：`chunk_id`, `parent_id`, `section_path`, `node_kind`, `entity_ids[]`。

树结构：`document → section → paragraph → sentence`（+ `table` / `figure` / `caption`）。

切片是检索与 Citationware 的原子单位（`chunk_id` → section / 父节点可回溯）。

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
