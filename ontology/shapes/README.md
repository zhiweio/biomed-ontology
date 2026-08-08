# SHACL（质量门入口）

权威 shapes：

| 路径 | 说明 |
|---|---|
| `schema/generated/*.shacl.ttl` | `task gen:shacl` 自 LinkML 生成 |
| `schema/shapes/projection.shacl.ttl` | 手调投影约束 |

本目录不存放第二套 shapes。校验入口：

```bash
task ontology:validate
```

入图前由 rdflib + pyshacl（及 Foundation sync 路径）执行 SHACL gate；**不使用 Jena**。
