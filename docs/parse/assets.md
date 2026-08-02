# 资产路径与视觉融合

源码：`src/biomed_ontology/parse/assets.py`。

## 为什么「渲染」而不是「抽取嵌入图」

科研图表常由矢量指令绘制，PDF 里根本没有「一张图」可抽，只有一堆线段。按 bbox **渲染页面区域**才能拿到人眼看到的那张图。

## 路径结构

```text
data/assets/<asset_dir_name(doc_id)>/<rel_path>
例: data/assets/DOC_PMC12133497/images/p0002_r000.png
```

切片里存的是**相对路径** `images/p0002_r000.png` —— 对每篇文档都长这样。相对的是该文档的 `out_dir`，不是 `data/assets/` 根。

## 事故课：44 → 0（无声）

`doc_id` 形如 `DOC:PMC…`。Windows/路径层不能含 `:`，落盘时要换成 `DOC_PMC…`。

曾经写入方与读取方**各写一遍** `replace`，或读取时漏掉 `doc_id` 段：

- 读不到文件 → `resolve_asset` 本应返回 `None`  
- 视觉列却退化成**编码 caption 文本**，照样产出像模像样的向量  
- 指标上看不出来「这一列到底看没看过像素」  
- 结果：几十张真图变成 0 张可读，报表还可能「不错」  

修复纪律：

!!! warning "有且只有一处拼路径"
    - `asset_dir_name(doc_id)` —— `:` / `/` → `_`  
    - `resolve_asset(root, doc_id, rel_path)` —— 唯一拼接  
    - Milvus 索引、图型标注、CLI 一律调用它，禁止再手拼  

## `safe_asset_name`

文件名白名单字符；压掉 `..`。文档正文不得参与路径拼接。

## AssetRecord 两字段

| 字段 | 可得性 |
|---|---|
| 像素 `rel_path` | 总有（渲染成功时） |
| VLM 摘要 `vision` | 仅配了 VLM 时 |

分成两个字段，避免「没摘要就当没图」。

## 如何验证

```bash
uv run pytest tests/test_parse_vision.py -q
# 索引前抽查：resolve_asset 对若干 IMAGE chunk 非 None
```
