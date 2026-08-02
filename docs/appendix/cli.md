# CLI 速查

入口：`uv run hmd`（`biomed_ontology.cli:app`）。细节与实测数字以 README 为准。

| 命令 | 作用 | 相关手册 |
|---|---|---|
| `hmd kb` | 构建知识库并打印统计 / warnings | [pipeline](../architecture/pipeline.md) |
| `hmd demo [--id …]` | 演示场景（自带断言） | [tools](../agent/tools.md) |
| `hmd eval` | 归一化 + 检索消融 + targets | [ARMS](../eval/arms.md) |
| `hmd index` | 写入 Milvus（盖 embedder 戳） | [milvus](../retrieval/milvus.md) |
| `hmd serve` | REST + MCP | [serve](../agent/serve.md) |
| `hmd contract` | 导出 OpenAPI / MCP 描述符 | [linkml](../architecture/linkml.md) |
| `hmd signals` | 演进信号与 KGCL | [evolution](../evolution/loop.md) |
| `hmd parse` | 单篇 PDF → 语料 YAML | [layout](../parse/layout.md) |
| `hmd sources` | 注册表与采购插槽 | [tiers](../licensing/tiers.md) |

## 常用组合

```bash
hmd eval --entitlements MOCK_LICENSED --milvus \
  --embedder multimodal-bio --reranker bge-reranker-v2-m3

hmd index --embedder multimodal-bio --figure-typer biomedclip --recreate

# 仅验证接线（不可入报告）
hmd index --embedder fake --allow-fake --recreate

hmd serve --port 8000
```

## Make 目标

| 目标 | 作用 |
|---|---|
| `make check` | ruff + 全量测试 |
| `make docs` / `docs-serve` | 手册严格构建 / 预览 |
| `make milvus-up` / `down` | 单机 Milvus |
| `make gen` | LinkML → `_generated/` |

手册预览：`make docs-serve` → http://127.0.0.1:8000
