# 双面 Eval 标准

入口：`uv run hmd eval --entitlements MOCK_LICENSED`  
源码：`biomed_ontology.eval.suite.run_dual_eval`

## 与 golden-eval 的分工

| | `hmd eval` | `hmd foundation golden-eval` |
|---|---|---|
| 问题 | 文献本体值多少？身份金标稳不稳？跨面同 ENT？ | 联调栈金路径通不通？ |
| 套件 | Identity · Literature(ARMS) · Bridge | resolve→context→evidence→assets |
| 后端 | 本地臂 + 可选 Milvus；WM **词典 ER** | **强制** GraphDB + Milvus + OM |
| 不做 | `get_entity_context` / backends_* / BIOS 全量 | nDCG 消融 / T1–T5 |

两者互补：删掉 eval 会丢掉文献科学与 waiver 纪律；删掉 golden-eval 会丢掉运行时诚实性门禁。

## 三套件

1. **Identity** — `data/gold/resolve.yaml` → `FoundationApi.resolve_entity`；I1 = gate cases 全对  
2. **Literature** — 原 ARMS + `normalization.yaml` + T1–T5（见 [arms](arms.md) / [targets](targets.md)）  
3. **Bridge** — `data/gold/bridge.yaml`：KB∧WM 同 ENT、resolve→search、许可 restore  

## 常用命令

```bash
uv run hmd eval --entitlements MOCK_LICENSED
uv run hmd eval --suite identity,bridge --no-retrieval
uv run hmd eval --json
uv run hmd foundation golden-eval   # WM 三后端联调
```
