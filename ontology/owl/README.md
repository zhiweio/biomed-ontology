# OWL（Protégé 审阅入口）

**不要在此目录手写企业 TBox 作为真相源。**

权威 OWL 由 LinkML 生成：

```bash
task gen:owl
# 产物：
#   schema/generated/hmd_enterprise.owl.ttl
#   schema/generated/hmd_concept.owl.ttl
#   …
```

## Protégé

1. `task gen:owl`
2. 用 Protégé 打开 `schema/generated/hmd_enterprise.owl.ttl`
3. 审阅 class / property / axiom
4. **若需改语义：回到 `schema/hmd_enterprise.yaml` 修改后重新 `task gen`**
5. 禁止把 Protégé 另存的 TTL 检入为 SSOT

可选：将生成物同步到本目录供离线分发：

```bash
task ontology:sync-artifacts
```
