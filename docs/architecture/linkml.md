# LinkML 与生成物

源码与契约：`schema/*.yaml` → `task gen` → `src/biomed_ontology/_generated/`。

LinkML 是本仓库跨 Python 运行时、OpenAPI/MCP 契约、RDF/SHACL 互操作的**唯一 schema SSOT**。业务模块只 import 生成物，禁止手写第二份枚举。

---

## 1. 为什么存在

本仓库同时需要：

- Python 运行时类型（Pydantic）
- JSON Schema（OpenAPI / MCP 工具契约）
- OWL / SHACL（对外语义互操作与入图闸门）

三套手写必然漂移。LinkML 一份 schema，生成多份制品；**业务约束写在 schema 描述里**（含设计决策 D1–D12 的叙述），比另起 ADR 目录更不容易和实现脱节。

Foundation 热路径另有 dataclass 适配层（`foundation/models.py`），与 schema 对齐；契约变更仍以 YAML → `task gen` 为准。

---

## 2. 设计取舍

| 决策 | 理由 |
|---|---|
| LinkML 唯一 SSOT | Protégé / OWL 只读生成物，不回写 |
| 不引入 Apache Jena | rdflib + pyshacl + GraphDB 承担 RDF 工程 |
| 枚举只存在于 `_generated/` | 禁止业务模块再定义 `LicenseTierEnum` 等同名枚举 |
| `TOOL_SPECS` 与 `hmd_tools.yaml` 一一对应 | MCP 契约与实现同步 |
| 设计决策写进 Field description | 读生成代码即见 D10 等约束 |

---

## 3. 设计与实现

### 3.1 文件地图

| Schema | 管什么 | 生成物 |
|---|---|---|
| `hmd_types.yaml` | 共享类型、CURIE 模式、跨模块枚举（被其余 schema import） | 无独立模块；并入各生成物 |
| `hmd_concept.yaml` | 概念、同义词、映射、层级、等价团 | `_generated/hmd_concept.py` |
| `hmd_fact.yaml` | 文档、切片、事实、检索通道枚举 | `_generated/hmd_fact.py` |
| `hmd_tools.yaml` | **KB 面 8 工具**请求/响应、Provenance | `_generated/hmd_tools.py` |
| `hmd_obs.yaml` | Trace / Decision / ToolIo / Signal | `_generated/hmd_obs.py` |
| `hmd_taxonomy.yaml` | 文档标引标签 | `_generated/hmd_taxonomy.py` |
| `hmd_enterprise.yaml` | Enterprise Ontology（DrugCandidate / Claim…） | `_generated/hmd_enterprise.py` |

`hmd_tools.yaml` 只覆盖文献/术语面 `TOOL_SPECS`（8）。Foundation 另有 10 个 `SEMANTIC_OPS`；双面合计 18 个具名操作，见 [Semantic Access](../tools/tools.md) 与 [策展资产与运行时机制](../ontology/curation-and-runtime.md)。

生成管线：`Taskfile` 的 `gen` target → Python 在 `_generated/`，OWL / JSON Schema / SHACL 在 `schema/generated/`（每源通常 `*.owl.ttl` / `*.schema.json` / `*.shacl.ttl`）。

手写投影约束：`schema/shapes/projection.shacl.ttl`（SKOS/PROV 入图形态；与 gen-shacl 实例 shapes 分离）。

可选 `task ontology:sync-artifacts` 复制 OWL/SHACL 到 `ontology/` 供离线分发。

### 3.2 消费方

| 消费方 | 导入示例 | 用途 |
|---|---|---|
| `ingest/seed.py` | `EntityTypeEnum`, `LicenseTierEnum` | 目录构建 |
| `search/__init__.py` | `RetrievalChannelEnum` | 混合检索通道 |
| `tools/api.py` | `hmd_tools` Request/Response | MCP / REST |
| `foundation/sync.py` | enterprise 类与 claim 字段 | TTL 序列化 |
| `quality/` | SHACL shapes | 入图闸门 |

### 3.3 改契约流程

```bash
# 1. 改 schema/*.yaml
# 2. 重新生成
task gen
# 3. 跑契约与 API 测试
uv run pytest tests/test_tools.py tests/test_service.py -q
```

新增 Semantic 工具时必须同时改：

1. `hmd_tools.yaml` 里的 Request/Response
2. `tools/api.py` 的 `TOOL_SPECS` 一行
3. `ToolApi` 实现 + `_invoke` 注册
4. 相关测试与手册工具计数

### 3.4 与 Ontology Toolchain 的关系

```text
schema/*.yaml  ──task gen──►  _generated/*.py
                │              OWL / SHACL / JSON Schema
                ▼
         ontology:validate
                ▼
         hmd foundation sync → GraphDB
                ▼
         Protégé（可选，只读审阅生成 OWL）
```

详见 [Ontology Toolchain](../ontology/toolchain.md)、[Foundation](foundation.md)。

### 3.5 典型枚举（以生成代码为准）

| 枚举 | 影响面 |
|---|---|
| `LicenseTierEnum` | 命名图隔离、查询重写、导出闸门 |
| `RetrievalChannelEnum` | BM25 / DENSE / GRAPH / FUSED |
| `SynonymScopeEnum` | 精确归一 vs expand |
| `MappingJustificationEnum` | 归一化 trace |
| `PredicateEnum` | SSSOM 映射 / clique 建团 |

---

## 4. 不变量与失败模式

| 不变量 | 说明 |
|---|---|
| 禁止手改 `_generated/` | 下次 `task gen` 覆盖，审查看不出意图 |
| schema 与 TOOL_SPECS 同步 | 漏改 = 对外契约撒谎 |
| Enterprise schema 与 Foundation 对齐 | `hmd_enterprise.yaml` 变更需 sync + 集成测 |
| SHACL 与入图数据同版本 | validate 失败则 sync 应阻断 |

| 失败模式 | 处理 |
|---|---|
| gen 后测试红 | 先修消费方，再提交 schema |
| 双份枚举定义 | mypy/ruff 可能不拦，运行时行为分裂 |
| Protégé 回写 OWL | **禁止**作为 SSOT |

---

## 5. 如何验证

```bash
task gen
task ontology:validate
uv run pytest tests/test_tools.py tests/test_service.py -q
uv run pytest tests/test_generated_schema.py -q 2>/dev/null || true
```

完整决策索引：[附录 · D1–D12](../appendix/decisions.md)。
