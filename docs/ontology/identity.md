# IdentityService

源码：`src/biomed_ontology/identity.py`。
目录级联：`src/biomed_ontology/normalize/`。
企业 ER：`src/biomed_ontology/foundation/resolve.py`。
装配：`runtime.open_dual_surface()` → `IdentityService.from_world(world)`。

---

## 1. 为什么存在

文献面要把 chunk / query 落到 `HMD:ENT:*`；Foundation 面要把「HMPL-504」「赛沃替尼」落到同一企业主键。
若两面各自装一份词典，会出现：

- 同一别名在检索命中 A、在 resolve 命中 B
- `open_dual_surface` 维护两套字典，发版后一边更新一边忘
- Agent 不知道该调 `normalize_entity` 还是 `resolve_entity`

`IdentityService` 是双面身份的**单一句柄**：词典只装配一次。

---

## 2. 设计取舍

| 决策 | 理由 | 放弃 |
|---|---|---|
| 一个服务、两套方法 | 级联算法不同，身份空间相同 | 强行把 ER 塞进 Normalizer |
| `from_world` / `from_catalog` | demo/eval/serve/lake 同一装配 | 各入口 `Normalizer(...)` |
| 无 Resolver 时 `resolve_text` 回落目录 normalize | 文献-only 路径仍可用 | 无 Resolver 就抛错 |
| 不从文档 mint ENT | 身份可策展、可发版 | PDF 自动发明 `HMD:ENT:*` |

---

## 3. 设计与实现

### 3.1 句柄

```text
IdentityService
├── normalizer: Normalizer          # ontology/catalog/ 级联
└── resolver: EntityResolver | None # dictionary + BERN2 + Zingg + xref
```

| 方法 | 走哪条链 | 典型调用方 |
|---|---|---|
| `concept(id)` | 目录查找 | 工具 / 测试 |
| `normalize(text, **kwargs)` | 词典 → 规则 → 向量 → 消歧 | 文献 chunk 挂载、`_ground`、查询改写 |
| `resolve_text(text, type_hint=?)` | 有 Resolver 走 ER；否则回落 `normalize` | Foundation `resolve_entity`、lake annotate |

`normalize` 未传 `ctx` 时自动建 `TraceContext`，避免调用方漏埋点。

### 3.2 两条链，同一身份空间

```text
文本
  ├─ IdentityService.normalize
  │    → Normalizer（catalog + ambiguity）
  │    → BuiltConcept.concept_id = HMD:ENT:*
  │
  └─ IdentityService.resolve_text
       → EntityResolver
            enterprise_id → xref → dictionary → zingg → bern2_dictionary → unmapped
       → EnterpriseEntity.id = HMD:ENT:*
```

两边的 ID 规则一致（`enterprise_id_for`）。差别在**级联阶段与后端**：

| | 目录级联 | 企业 ER |
|---|---|---|
| SSOT | `ontology/catalog/` | `ontology/entities/` + `dictionary/` |
| 消歧 | `ambiguity.yaml` + ContextDisambiguator | alternatives / 低置信，禁止静默单选 |
| 公共 NER | 不吃 BERN2 当身份源 | BERN2 只出候选外部 ID |
| 查询面 | `normalize_entity` / `expand_concept` | `resolve_entity` / `get_entity_context` |

公开 BIOS 概念走 `lookup_bios_concept`，**不**经 IdentityService mint ENT。

### 3.3 装配

```text
open_dual_surface()
  → load_world_model()
  → IdentityService.from_world(world)     # 目录 + world.resolver
  → foundation.identity = identity
  → DualSurface.identity = identity

lake/steps.write_claims
  → IdentityService.from_catalog()        # 入湖接地，不另装第二份 Normalizer
```

`from_catalog()` 调用 `ingest.catalog.load_catalog_normalizer()`。

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| 词典只装配一次 | 禁止 serve/eval 再 `Normalizer(...)` |
| 企业主键只有 `HMD:ENT:*` | BIOS / UMLS / HGNC 只 xref |
| 文档不 mint ENT | 挂不上 → unmapped / 演进信号 |
| 不确定不猜 | D3：返回 `alternatives` |
| BROAD 不进精确归一 | D2：scope 驱动扩展 |

| 失败模式 | 表现 |
|---|---|
| catalog 与 entities 不同步 | normalize 与 resolve 落到不同 ID |
| 把 BERN2 当 Normalizer 词典 | 身份随公共服务漂移 |
| 无 Resolver 却当 ER 已完成 | `resolve_text` 只是目录 normalize |

---

## 5. 如何验证

```bash
uv run pytest tests/test_identity.py tests/test_normalize*.py tests/test_runtime_dual_surface.py -q
uv run hmd foundation resolve "HMPL-504"
uv run hmd demo --id W1
```

相关：[目录 SSOT](seed.md)、[归一化级联](normalize.md)、[策展与运行时](curation-and-runtime.md)、[Foundation](../architecture/foundation.md)。
