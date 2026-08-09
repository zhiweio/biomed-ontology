# 双面 Eval 标准

入口：`uv run hmd eval --entitlements MOCK_LICENSED`  
源码：`biomed_ontology.eval.suite.run_dual_eval`；装配：`open_dual_surface()`（`runtime.py`）。

## 为什么存在

产品定位是 **World Model + Ontology Semantic Layer**。单一检索 benchmark 无法回答：

- 企业实体 `resolve_entity` 是否稳定落到金标 `HMD:ENT:*`？
- 文献检索里本体通道值多少（可消融、可显著性）？
- KB 归一化与 WM resolve 是否指向同一 ENT？许可还原是否泄漏？

因此 `hmd eval` 编排 **Identity · Literature · Bridge** 三套件；World Model 三后端联调则独占 `hmd foundation golden-eval`，避免把「栈通不通」与「文献科学」混在一张表里。

## 设计取舍

| 取舍 | 选择 | 放弃 |
|---|---|---|
| 文献后端 | Milvus 臂；不可达标「未运行」 | 内存词法顶替 Milvus |
| WM eval 身份 | 词典 ER（与生产默认同路） | eval 里偷偷开 BERN2 全量 |
| 三后端门禁 | `golden-eval` 强制 GraphDB + Milvus + OM | 在 `hmd eval` 里跑 `get_entity_context` |
| 报告形态 | Rich + `--json` + targets 门禁 | 只 stdout 一段数 |
| 实测数字 | 写在 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md) | 手册抄表（易过期） |

## 设计与实现

### 与 golden-eval 的分工

| | `hmd eval` | `hmd foundation golden-eval` |
|---|---|---|
| 问题 | 文献本体值多少？身份金标稳不稳？跨面同 ENT？ | 联调栈金路径通不通？ |
| 套件 | Identity · Literature(ARMS) · Bridge | resolve→context→evidence→assets |
| 后端 | 本地臂 + Milvus；WM **词典 ER** | **强制** GraphDB + Milvus + OM |
| 不做 | `get_entity_context` / backends_* / BIOS 全量 | nDCG 消融 / T1–T5 |

两者互补：删掉 eval 会丢掉文献科学与 waiver 纪律；删掉 golden-eval 会丢掉运行时诚实性门禁。

### 运行时装配

`run_dual_eval(surface, …)` 接收 `open_dual_surface()` 返回的 `DualSurface`：

- `surface.tools` → `ToolApi`（文献检索、normalize 等）；
- `surface.foundation` → `FoundationApi`（Identity / Bridge 的 resolve）；
- 文献检索要求 **Milvus** 已索引（或测试注入 `milvus_backend`）。

```text
open_dual_surface()
  ├── ToolApi + HybridSearcher(Milvus)
  └── FoundationApi(world)
         ↓
run_dual_eval(surface, entitlements=…)
  ├── eval_identity(foundation)
  ├── eval_normalization + eval_retrieval(tools, milvus_backend=…)
  └── eval_bridge(tools, foundation, entitlements)
```

### 三套件

#### 1. Identity

- 数据：`data/gold/resolve.yaml`
- 调用：`FoundationApi.resolve_entity`
- 硬门禁 **I1**：gate cases `accuracy == 1.0`（`IdentityEval.gate_ok`）

#### 2. Literature

- 数据：`data/gold/retrieval.yaml` + `normalization.yaml`
- 内容：ARMS 消融（见 [arms](arms.md)）+ 归一化准确率 + T1–T5（见 [targets](targets.md)）
- 输出：`RetrievalEval`（分 `by_lang` / `by_intent` / `by_probe`）+ 配对显著性（见 [significance](significance.md)）

#### 3. Bridge

- 数据：`data/gold/bridge.yaml`
- 校验：KB `normalize` 与 WM `resolve` 同 ENT；resolve 后文献可检索；许可 `restore_context` 不泄漏
- 硬门禁 **B1**：`alias_ok ∧ literature_ok ∧ entitlement_ok`

### `DualEvalReport.ok` 逻辑

| 子报告 | 通过条件 |
|---|---|
| `identity_ok` | 未跑或 `gate_ok` |
| `literature_ok` | targets（met 或 waived，且无 stale_waiver）+ 全臂 `citation_fidelity >= 1.0`；unavailable 目标不参与拖垮 |
| `bridge_ok` | 未跑或 `bridge.ok` |

### 常用命令

```bash
uv run hmd eval --entitlements MOCK_LICENSED
uv run hmd eval --suite identity,bridge --no-retrieval
uv run hmd eval --json
uv run hmd foundation golden-eval   # WM 三后端联调（不并入 eval）
```

## 不变量与失败模式

| 不变量 | 违反后果 |
|---|---|
| Milvus 臂不可回落内存 | `unavailable` 明示，非假 0 分 |
| T5 citation_fidelity = 1.0 | literature_ok 失败 |
| I1 / B1 硬门禁 | `ok=false` 即使 Literature 很漂亮 |
| eval 不跑 golden-eval 路径 | 避免用 YAML 假扮三后端 |
| 凭据 `MOCK_LICENSED` | Bridge 许可用例可测；无凭据时 entitlement 失败 |

失败模式：

- **只跑 `--no-retrieval`**：看不到 T1–T5，只能验 Identity/Bridge。
- **Milvus 未起**：多臂 `unavailable`；读表时勿当 0 分。
- **把 golden-eval 绿当作 eval 绿**：栈通 ≠ 本体增益成立。

## 如何验证

```bash
uv run pytest tests/test_eval_targets.py tests/test_eval_demo.py tests/test_runtime_dual_surface.py -q
uv run hmd eval --entitlements MOCK_LICENSED
uv run hmd eval --json | jq .ok
```

实测分数与表格见 [README](https://github.com/zhiweio/biomed-ontology/blob/main/README.md#双面-eval)；读数方法见 [gold](gold.md)、[significance](significance.md)。
