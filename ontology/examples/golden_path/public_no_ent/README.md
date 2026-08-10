# Golden Path：公开 CURIE、无 HMD:ENT:*

验证「未策展进企业本体」时的默认路径：

```text
公开 CURIE → lookup_bios_concept → search_surfaces
           → resolve_entity（canonical=null，附 surfaces）
           → search_documents（expansion_source=public_lexical）
```

**不** mint `HMD:ENT:*`，**不**把公开 BIOS 当 GRAPH 种子。

对照有 ENT 的金路径见 [`../hmpl504/`](../hmpl504/)。

## 一步跑通（离线）

```bash
uv run hmd foundation resolve "CHEBI:DEMO_ASPIRIN"
# 期望：canonical_entity=null，search_surfaces 含 aspirin

uv run hmd foundation lookup-bios --query "阿司匹林"
uv run hmd foundation lookup-bios --external-id CHEBI:DEMO_ASPIRIN
```

Demo / Eval：

```bash
uv run hmd demo W3
uv run hmd eval --suite public_bios --no-retrieval
```

## 期望答案（最小）

见 [`expected.json`](expected.json)：

| 维度 | 期望 |
|---|---|
| BIOS | `BIOS:ASPIRIN_DEMO` |
| enterprise_bridges | `[]` |
| resolve | `canonical_entity: null` |
| search_surfaces | 含 `aspirin` |
| catalog normalize | abstain（不进企业种子） |
| search rewrite | `expansion_source=public_lexical` |

对照有桥接的公开 CURIE：`NCBIGene:4233` → `HMD:ENT:TGT:MET`（exact xref）。
