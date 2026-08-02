# 版面后端与语义树

源码：`src/biomed_ontology/parse/`（`layout/`、入口 `parse/__init__.py`）。

## 为什么要版面层

PDF 不是「纯文本 + 附图」。同一页上混着双栏正文、图表、页眉、脚注。没有版面结构就直接切块：

- 图表说明和正文揉在一起  
- 页眉「Copyright…」污染 BM25  
- 图像区域不知道 bbox，无法渲染像素给视觉列  

版面后端产出**语义树**（节 / 段 / 图 / 表），再落到语料 YAML。

## 后端闸门

| 后端 | 形态 | 许可 |
|---|---|---|
| PyMuPDF | 本地库 | AGPL / 商业双授权，`COMPONENTS` pending |
| MinerU | **纯 HTTP 客户端，绝不 import mineru** | 附加商业门槛，pending |

MinerU 进程外置：避免把 AGPL/附加条款传染进本仓库 import 图，也便于内网只暴露 HTTP。

启用前走 `assert_component_cleared`；本地试用见 [组件闸门](../licensing/components.md)。

## 语义树不变量

- bbox 拿不到时留空元组，**绝不伪造整页坐标**（假 bbox 会渲染出整页噪声图）  
- 图像文件名只由页码与序号拼，**绝不取自文档内容**（路径穿越）  
- 解析产物落 `data/corpus/parsed/`；装配必须扫到该目录（见 [pipeline](../architecture/pipeline.md)）  

## 与 knowhere 的关系

布局/解析路径衍生自 [Ontos-AI/knowhere](https://github.com/Ontos-AI/knowhere)（Apache-2.0），修改见 `NOTICE`。组件 review=cleared。

## 如何验证

```bash
uv run pytest tests/test_parse.py tests/test_parse_layout.py tests/test_parse_pymupdf.py tests/test_parse_mineru.py -q
uv run hmd parse --help
```
