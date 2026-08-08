# Golden Path：HMPL-504（savolitinib）

验证一条真实研发链路：

```text
DrugCandidate → Target → Disease(Indication) → Evidence → ELN/LIMS Asset
```

文档示意中的 `ABC-001` 对应本仓库种子实体 **HMPL-504 / `HMD:ENT:DC:savolitinib`**。

## 一步跑通

```bash
# 可选：联调栈（GraphDB / Milvus / OpenMetadata）
# export HMD_BIOS_LICENSE_ACK=poc
# task foundation:up

uv run hmd foundation golden --candidate HMPL-504          # Rich 分步推理展示
uv run hmd foundation golden --candidate HMPL-504 --compact  # 仅 Trace
uv run hmd foundation golden --candidate HMPL-504 --json     # 机器可读
# 或
uv run hmd foundation serve --mcp   # :8100  REST + MCP get_entity_context
```

离线（仅 YAML seed）即可验证解析与聚合；不依赖 Docker。

## 期望答案（最小）

见 [`expected_context.json`](expected_context.json)：

| 维度 | 期望 |
|---|---|
| canonical | `HMD:ENT:DC:savolitinib` |
| target | `HMD:ENT:TGT:MET`（HGNC:7029） |
| disease | `HMD:ENT:IND:nsclc` |
| evidence | PubMed + Patent + ELN（带 claim / span） |
| assets | `asliva.eln.exp_2025_012`、`asliva.lims.asy_001` |

## BERN2 / 词典输入（离线）

```text
HMPL-504 inhibits MET signaling in NSCLC.
```

词典路径（无需 BERN2 服务）应解析：

| mention | enterprise_id |
|---|---|
| HMPL-504 | `HMD:ENT:DC:savolitinib` |
| MET | `HMD:ENT:TGT:MET` |
| NSCLC | `HMD:ENT:IND:nsclc` |

```bash
uv run hmd foundation resolve "HMPL-504"
uv run hmd foundation resolve "MET"
uv run hmd foundation resolve "NSCLC"
```

## MCP

```bash
uv run hmd foundation serve --mcp
# 工具：get_entity_context(enterprise_id="HMD:ENT:DC:savolitinib")
```

详述：[`docs/ontology/golden-path.md`](../../../../docs/ontology/golden-path.md)。
