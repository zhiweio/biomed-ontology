# LinkML 与生成物

源码与契约：`schema/*.yaml` → `src/biomed_ontology/_generated/`（`make gen`）。

## 为什么用 LinkML 当 SSOT

本仓库同时需要：

- Python 运行时类型（Pydantic）  
- JSON Schema（OpenAPI / MCP 工具契约）  
- 可选 OWL / SHACL（对外语义互操作）  

三套手写必然漂移。LinkML 一份 schema，生成多份制品；**业务约束写在 schema 描述里**（含设计决策 D1–D12 的叙述），比另起 ADR 目录更不容易和实现脱节。

## 文件地图

| Schema | 管什么 | 生成物 |
|---|---|---|
| `hmd_concept.yaml` | 概念、同义词、映射、许可枚举 | `_generated/hmd_concept.py` |
| `hmd_fact.yaml` | 文档、切片、事实、检索通道枚举 | `_generated/hmd_fact.py` |
| `hmd_agentapi.yaml` | 11 工具请求/响应、Provenance | `_generated/hmd_agentapi.py` |
| `hmd_obs.yaml` | Trace / Decision / ToolIo | `_generated/hmd_obs.py` |
| `hmd_taxonomy.yaml` | 文档标引标签 | `_generated/hmd_taxonomy.py` |

枚举（`LicenseTierEnum`、`RetrievalChannelEnum`、`SynonymScopeEnum`…）以生成代码为准；业务代码 `from biomed_ontology._generated...` 导入，**禁止在业务模块再定义一份同名枚举**。

## 改契约的正确流程

```bash
# 1. 改 schema/*.yaml
# 2. 重新生成
make gen
# 3. 跑契约与 API 测试
uv run pytest tests/test_agentapi.py tests/test_service.py -q
```

!!! warning "不要手改 _generated/"
    生成目录视为构建产物。手改会在下次 `make gen` 被覆盖，且审查时看不出意图。
    需要新字段：改 YAML → gen → 再改消费方。

## 与 Agent 工具清单的对齐

`TOOL_SPECS`（`agentapi/__init__.py`）与 `schema/hmd_agentapi.yaml` **一一对应**，供 MCP / OpenAPI 自动生成。新增工具时必须同时改：

1. schema 里的 Request/Response  
2. `TOOL_SPECS` 一行  
3. `AgentApi` 实现 + `_invoke` 注册  
4. README / 手册里的「11」—— 有测试绊线  

漏改 schema = 运行时能调、对外契约却撒谎。

## 设计决策为何写在 schema 里

例如 `LicenseTierEnum` 的描述直接写「决定 RDF named graph 隔离、查询重写、导出闸门与训练语料准入」。读生成出的 Field description，就能看见 D10，而不必先找到某份过期 wiki。

完整索引见 [附录 · D1–D12](../appendix/decisions.md)。
