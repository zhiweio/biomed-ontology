# 图型 figure_type

源码：`src/biomed_ontology/parse/figure_type.py`。

## 存在的理由（实测缺陷）

查询「chest CT scan showing a pulmonary nodule」，加上 `modalities=[IMAGE]` 之后，首位仍可能是「Top 30 PT 信号强度」柱状图。

- `modality` 保证返回的是**图**  
- 不保证是**那一类**图  
- 向量相似度在这件事上不可靠：CT 与柱状图在图文空间的距离常被 caption 词主导，而 caption 往往不提图型  

图型是布尔条件，与 `modality` 同性质 → **落成标量下推**，不往分数里掺说不清的偏置。

## 类型集合（刻意粗）

```text
RADIOLOGY | MICROSCOPY | GROSS_PATHOLOGY | CHART
GEL_BLOT | DIAGRAM | TABLE_IMAGE | OTHER
```

细到「轴位增强 CT」没有任何 query 用得上，却会让零样本在近义标签间横跳。  
`GROSS_PATHOLOGY` 与 `MICROSCOPY` 分开：实测切除标本照片在只有镜检标签时被判成镜检 —— 检索意图完全不同。

## 判定路径

1. **BiomedCLIP 零样本**（主路径）—— 训练集 PMC-15M 与本域插图分布接近；每类多条 prompt，取同类最高分  
2. **Caption 关键词兜底** —— 不是因为更准，而是没有 GPU/权重时字段不能恒为空（恒空比没有该字段更糟）  

`get_figure_typer("biomedclip" | "keyword" | …)`；索引 CLI：`--figure-typer biomedclip`。

BiomedCLIP 组件 `review=pending`，见 [组件闸门](../licensing/components.md)。

## 与第五列的分工

| | `figure_type` 过滤 | `dense_visual_bio` 列 |
|---|---|---|
| 角色 | 布尔门禁 | 在允许集合内排序 |
| 失败模式 | 漏滤 → 柱状图进 CT 查询 | 分布不匹配时净值可为负 |

先滤再排；不要指望第五列「自己懂 CT」。

## 如何验证

```bash
uv run pytest tests/test_figure_type.py -q
uv run hmd index --recreate   # 默认 --figure-typer biomedclip
```
