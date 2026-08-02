# 模态与图型过滤

## 为什么是布尔条件，不是偏好

「我要看那张 CT」不是「更偏好图像一点」—— 混排结果里夹一张 Kaplan-Meier 曲线，调用方无法用「调权重」修好。`modality` 与 `figure_type` 与许可一样，是**硬过滤**。

## 两道闸

1. **后端下推** —— Local：allow_list；Milvus：标量 `expr`  
2. **进程内终闸** —— `HybridSearcher.search` 在 RRF 之后、截断之前再滤一次  

终闸存在的理由：`SearchBackend` 是 Protocol，一个忽略 `modalities` 的实现不该让「只看图」悄悄退化成混排。放在截断前，避免把 `top_k` 砍薄还以为召回不够。

图通道走 `_graph_allowed`，复用同一组 modality / figure_type / license / labels 条件 —— **少过滤一条通道，脏结果无法归因**。

## 与向量列的关系

- 过滤：布尔，决定候选能不能进池  
- 视觉列：在已允许的图像里排序  

常见误用：不加 `figure_types=("RADIOLOGY",)`，却指望 BiomedCLIP 列「自己懂」—— 生医视觉列可能把生存曲线顶到第一（机制上相关、任务上错误）。路由见 [figure_type](../parse/figure-type.md)。

## labels

文档 taxonomy 标签同样可过滤；图通道与后端同一套 `wanted & labels` 交集逻辑。

## 如何验证

集成测试覆盖「过滤后无泄漏」；手工：

```bash
# 概念上：search(..., modalities=("image",), figure_types=("RADIOLOGY",))
uv run pytest tests/test_parse_vision.py tests/test_figure_type.py -q
```
