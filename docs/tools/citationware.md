# Citationware：证据树与 restore_context

源码：`src/biomed_ontology/tools/citation.py`（设计决策 D6）。

检索返回高匹配碎片；碎片能证明「有这句话」，却证明不了语境。  
Citationware 负责：文档 → 章节 → 碎片的证据树，以及 `restore_context` 还原整节原文。

许可谓词与检索共用同一 `LicenseScope.permits` —— 还原不得成为用碎片 id 换受限全文的后门。

## 如何验证

```bash
uv run pytest tests/test_citation.py -q
uv run hmd demo --id D7           # Rich 面板
uv run hmd demo --id D7 --compact # 仅 Trace
```
