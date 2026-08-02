# REST / MCP 与凭据边界

源码：`src/biomed_ontology/service/`，CLI：`hmd serve`。

## 暴露面

| 协议 | 形态 |
|---|---|
| REST | `POST /v1/{tool_name}` × 11 |
| MCP | 同名工具，供 Agent 运行时挂载 |

两者共用 `AgentApi` 实现，避免「REST 过闸门、MCP 不过」的分叉。

## 凭据（entitlements）怎么进系统

调用方持有的是**源 ID 集合**（如 `MOCK_LICENSED`、未来的 `UMLS`）。  
`licensing.max_visible_tier`：无凭据 → 最高可见 TIER_1；有凭据 → 可达 TIER_3（仍受各源 partition / named graph 约束）。

!!! warning "凭据不是角色名"
    不要发明 `admin` 这种绕过 tier 的超级角色。突破许可边界只能来自明确的源订阅凭据 + 代码审查。

## 进程生命周期

1. 启动：`build_knowledge_base()` 一次  
2. 可选：连接已盖戳的 Milvus 集合  
3. 每个请求：新 `TraceContext`，带上该请求的 entitlements  
4. 关闭：不在请求路径上重建 KB  

## 构建期 vs 运行期

| | 构建期 | 运行期 |
|---|---|---|
| 网络 | 可拉快照 / 权重 | **完全内网离线**预期 |
| 组件闸门 | `assert_component_cleared` | 同左；`HMD_ACCEPT_UNCLEARED_COMPONENTS` 仅本地留痕试用 |

## 如何验证

```bash
uv run pytest tests/test_service.py -q
uv run hmd serve   # 本地起服务后打 /v1/normalize_entity 等
```
