# 快速开始

本页是检查清单与「第一天会踩的坑」。完整命令与实测数字见仓库 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md)。

## 你在搭什么

不是 chatbot。你在搭：

1. 一份可遍历的本体（概念 + 类型化链接）  
2. 一份带许可的语料切片  
3. 一条可解释的混合检索  
4. 一组带 provenance 的 Agent 工具  

跑通下面闭环，再读机制章。

## 最小闭环

```bash
uv sync --extra dev --extra rdf --extra ontology --extra parse --extra vector --extra service

uv run hmd kb        # 构建知识库：看 stats + warnings
uv run hmd demo      # 8 个演示场景（自带断言，不是打印）
uv run hmd eval --entitlements MOCK_LICENSED
uv run hmd serve --port 8000
make check           # ruff + 全量测试
```

`hmd kb` 若出现未解析链接 / 未登记歧义，先看 [种子](ontology/seed.md)，再谈调检索。

## 可选：Milvus 五列 + 精排

```bash
make milvus-up
uv run hmd index --embedder multimodal-bio --figure-typer biomedclip --recreate
uv run hmd eval --milvus --embedder multimodal-bio \
    --reranker bge-reranker-v2-m3 --entitlements MOCK_LICENSED
```

权重解析：本地 → `HMD_MODEL_HUB` → Gitee 兜底。见 [嵌入器](retrieval/embedders.md)。

## 验收时你会碰到的纪律

| 现象 | 原因 | 不要做的事 |
|---|---|---|
| Milvus 臂「未运行」 | 容器没起或集合不存在 | 期待静默回落到本地 |
| 精排臂「未运行」 | 没传 `--reranker` | NullReranker 顶替后还写「+精排」 |
| `fake` 被拒绝 | 报告口径必须用真模型 | 验证接线时请显式 `--allow-fake` |
| 建表「最多 4 向量列」 | Milvus 默认上限 | 配 `PROXY_MAXVECTORFIELDNUM`（见 docker compose） |
| `LicenseViolation` 组件 | pending 未 accept | 本地设 `HMD_ACCEPT_UNCLEARED_COMPONENTS=true` |
| eval 直接拒绝出数 | gold 键 dangling | 用 `scripts/dump_sections.py` 对照 |

!!! warning "待法务核实"
    PyMuPDF、MinerU、BiomedCLIP：`review=pending`。详见 [组件闸门](licensing/components.md)。

## 建议阅读顺序（第一周）

1. [分层 L0–L8](architecture/layers.md) + [pipeline](architecture/pipeline.md)  
2. [links / search-around](ontology/links.md) + [hybrid RRF](retrieval/hybrid.md)  
3. [ARMS](eval/arms.md) + [不变量](invariants.md)  
4. 按你的任务选：Agent / Milvus / 许可  

手册预览：`uv sync --extra docs && make docs-serve`。
