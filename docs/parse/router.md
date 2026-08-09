# Document Router

源码：`src/biomed_ontology/parse/router.py`。

多格式企业文档经 Router 进入统一 `LayoutBlock` IR，再走既有 Semantic Tree → Canonical YAML → Tree Chunk 管线。

## 三路径

| 路径 | 后端 | 适用 |
|---|---|---|
| Fast | `pymupdf4llm` | 简单可提取文本的 PDF |
| Main | `docling` | 结构化 PDF + DOCX / PPTX / XLSX |
| Hard | `mineru`（默认 local，可 http） | 扫描件、复杂版面、图像 OCR |

`pymupdf` LayoutBackend **已废弃**；配置传入会报错。历史 YAML 中 `parse.backend: pymupdf` 只读兼容。

## 格式路由

| 后缀 | 首选 | 可降级（需 `HMD_LAYOUT_FALLBACK=true`） |
|---|---|---|
| `.pdf` | probe → Fast / Main / Hard | `pymupdf4llm → docling → mineru` |
| `.docx` / `.pptx` | Docling | MinerU |
| `.xlsx` | Docling | 无 |
| `.png` / `.jpg` | MinerU | Docling |

## PDF probe

廉价本地信号（页数、图数、表候选、文本可提取率、多栏启发式）。阈值：

- `HMD_PARSE_FAST_MAX_PAGES`（默认 40）
- `HMD_PARSE_FAST_MAX_IMAGES`（默认 8）
- `HMD_PARSE_FAST_MAX_TABLES`（默认 4）

## 配置一览（`HMD_` 前缀）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LAYOUT_BACKEND` | `auto` | `auto\|pymupdf4llm\|docling\|mineru` |
| `LAYOUT_FALLBACK` | `false` | 自动降级链 |
| `PARSE_FAST_MAX_PAGES` / `_IMAGES` / `_TABLES` | 40 / 8 / 4 | Fast Path probe |
| `PARSE_MAX_PAGES` / `PARSE_MAX_BYTES` | 400 / 64MiB | 攻击面上限 |
| `MINERU_TRANSPORT` | `local` | `local\|http` |
| `MINERU_BASE_URL` / `API_KEY` / `TIMEOUT_S` | localhost:8000 | 仅 HTTP |
| `MINERU_ENGINE` | `pipeline` | MinerU backend 名 |
| `MINERU_PARSE_METHOD` | `auto` | `auto\|txt\|ocr` |
| `MINERU_LANG` | `ch` | OCR 语言提示 |
| `MINERU_FORMULA_ENABLE` / `TABLE_ENABLE` | `true` | 模块开关 |
| `MINERU_EFFORT` | `medium` | hybrid：`medium\|high` |

完整注释见仓库根目录 [`.env.example`](../../.env.example)。

## Fallback

默认关闭。开启后：后端抛错，或 `degraded` 命中 `ocr|formula|table_structure|reading_order` 且块过少时，按链重试。每次尝试写入 `parse.route.attempts[]`。

## Canonical 溯源

```yaml
parse:
  backend: pymupdf4llm   # 实际成功者
  degraded: []
  route:
    requested: auto
    chosen: pymupdf4llm
    reason: simple_pdf
    probe: {...}
    attempts: [...]
```

## Office locator

`page` 为 1-based locator：PDF=页、PPTX=幻灯片序、XLSX=工作表序。细节在 `backend_meta.locator_kind`（`page|slide|sheet`）。**禁止伪造 bbox**。

## 验证

```bash
uv run pytest tests/test_parse_router.py tests/test_parse_layout.py tests/test_parse_pymupdf4llm.py -q
uv run hmd parse --help
```
