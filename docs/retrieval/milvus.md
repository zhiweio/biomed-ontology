# Milvus 五列与融合不下推

源码：`src/biomed_ontology/search/backends/milvus.py`，编排见 `docker/milvus-standalone.yml`。

## 为什么要向量后端

LocalBackend 用内存稀疏/伪稠密验证接线；真模型 + 真 ANN 需要外置索引。Milvus 负责**列式召回**，不负责业务融合与 explain。

## 五列是什么

| 列 | 通道映射 | 典型模型 | 度量 |
|---|---|---|---|
| `sparse_lexical` | BM25 | 词法稀疏 | IP |
| `dense_general` | DENSE | BGE-M3 | COSINE |
| `dense_biomed` | DENSE | SapBERT | COSINE |
| `dense_visual` | DENSE | Qwen3-VL | COSINE |
| `dense_visual_bio` | DENSE | BiomedCLIP | COSINE |

多条稠密列**共用** `RetrievalChannelEnum.DENSE`，但通过 `vector_fields` 分别启用，消融靠减法（见 ARMS 的 `milvus_hybrid_2col`…`5col`）。

`merge_best`：同一 chunk 被多列命中时保留最好分，再交给进程内 RRF。

## 融合为什么不下推

Milvus `RRFRanker` 更省事，但返回的融合分**无法反解**各通道名次 → `SearchHit.explain`（`RRF(bm25#3 + …)`）作废 → 可观测 WHY 支柱断裂。

因此后端只返回各列 `(chunk_id, score)` 列表，`HybridSearcher.rrf_fuse` 仍在进程内做。

## 许可：标量下推 + partition key

```text
expr: license_rank / source_id 条件
partition_key_field = source_id
```

- 表达式：无权查询根本不返回受限行  
- partition key：采购边界成物理边界；即使表达式写错，付费分区也不被无凭据查询触碰  

图通道在进程内用**同一套** `LicenseScope.permits`（`_graph_allowed`），否则图通道成为许可旁路。

## 集合盖戳（embedder 指纹）

集合 `description` 写入 `embedder=…`。索引与评测 embedder 不一致 → **直接退出**，而不是静默用错向量空间出一张漂亮报表。

`fake` 必须 `--allow-fake`：假嵌入器报表和真报表长得一样，开关是为了在命令行历史留痕。

## 五列与默认上限

Milvus 默认最多 4 个向量字段。五列需要：

```yaml
# docker/milvus-standalone.yml
PROXY_MAXVECTORFIELDNUM: "6"
```

连自建实例必须自己配，否则建表报 `maximum vector field's number should be limited to 4`。代码在失败路径上会提示该配置。

## 标量字段（不只是向量）

| 字段 | 用途 |
|---|---|
| `modality` | TEXT / IMAGE / … 布尔过滤 |
| `figure_type` | RADIOLOGY / CHART / … 布尔过滤 |
| `asset_path` | 命中视觉列时能还原原图（审计要图，不要只有分） |
| `section_id` / `page` | 溯源 |

`figure_type == ""` 语义是「未分类」，不是「不是图」。过滤时不要误用。

## 无静默回落

`--milvus` 臂在容器不可达时标「未运行」，**绝不回落**到 LocalBackend 还写着 Milvus 列名。README 与测试都守这条。

## 如何验证

```bash
make milvus-up
uv run hmd index --embedder multimodal-bio --recreate --figure-typer biomedclip
uv run hmd eval --milvus --embedder multimodal-bio --entitlements MOCK_LICENSED
uv run pytest tests/test_search_backend.py tests/test_milvus_license.py -q
```
