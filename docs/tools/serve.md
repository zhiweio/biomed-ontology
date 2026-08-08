# REST / MCP 与凭据边界

源码：`src/biomed_ontology/service/`。

## 唯一入口

```bash
uv run hmd serve --mcp          # 默认 :8000
```

同一进程暴露 **Semantic Access**（世界模型访问面）：

- Ontology Semantic Layer（KB tools：术语 / 扩展 / 事实 / 检索 / Citationware / feedback）
- Foundation Semantic Ops（GraphDB / Milvus / OM）
- MCP：`/mcp`

二者经 `build_state()` → `open_dual_surface()` 同进程装配（`ToolApi.from_backends` + `FoundationApi`），
避免「REST 过闸门、MCP 不过」或两套 KB 分叉。`/v1/golden_path` 会带上文献腿。

## 凭据

`X-HMD-Entitlements` **默认不被信任**（`HMD_TRUST_ENTITLEMENT_HEADER=false`）。  
生产环境由网关按已认证身份注入。

调用方身份头：`X-HMD-Client-Id`（旧 `X-HMD-Agent-Id` 仍作别名接受）。

## 契约导出

```bash
uv run hmd contract --out build/contract
```

产出 MCP 描述符与 OpenAPI（KB 工具以 LinkML 为准；Foundation ops 合并进同一 OpenAPI）。
