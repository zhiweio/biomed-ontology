# 版面后端与语义树

源码：`src/biomed_ontology/parse/`（`layout/`、`router.py`、入口 `parse/__init__.py`）。

## 为什么要版面层

PDF / Office 不是「纯文本 + 附图」。没有版面结构就直接切块：

- 图表说明和正文揉在一起  
- 页眉污染 BM25  
- 图像区域不知道 bbox，无法渲染像素给视觉列  

版面后端产出 **LayoutBlock**，再经语义树落到语料 YAML。路由见 [router](router.md)。

## 后端闸门

| 后端 | 形态 | 许可 |
|---|---|---|
| PyMuPDF4LLM | 本地库（Fast Path） | AGPL / 商业双授权（底层 PyMuPDF），`COMPONENTS` pending |
| Docling | 本地库（Main Path） | MIT，pending |
| MinerU | **本地库（默认）或 HTTP** | 附加商业门槛，pending |

MinerU 由 `HMD_MINERU_TRANSPORT=local|http` 选择：默认 `import mineru` 本地解析；`http` 时打自建 `mineru-api` / 云 API（语料出网告警见 Settings.warnings）。

启用前走 `assert_component_cleared`；本地试用见 [组件闸门](../licensing/components.md)。

## 语义树不变量

- bbox 拿不到时留空元组，**绝不伪造整页坐标**（假 bbox 会渲染出整页噪声图）  
- 图像文件名只由页码与序号拼，**绝不取自文档内容**（路径穿越）  
- 解析产物落 `data/corpus/parsed/`；装配必须扫到该目录（见 [pipeline](../architecture/pipeline.md)）  
- IR 是 Markdown 文本 + 逐块 provenance，**禁止**纯 Markdown（丢掉 bbox 会破坏 Citationware）

## 与 knowhere 的关系

布局/解析路径衍生自 [Ontos-AI/knowhere](https://github.com/Ontos-AI/knowhere)（Apache-2.0），修改见 `NOTICE`。组件 review=cleared。

## 如何验证

```bash
uv run pytest tests/test_parse.py tests/test_parse_layout.py tests/test_parse_pymupdf4llm.py tests/test_parse_mineru.py tests/test_parse_router.py -q
uv run hmd parse --help
```
