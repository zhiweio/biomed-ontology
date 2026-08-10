# Zingg batch link

离线模糊 ER：**不在** resolve 请求路径跑 Spark。

```text
ontology SSOT + Iceberg er_observations
  → hmd foundation zingg-run --mode materialize-only
  → data/zingg/input/{enterprise,observation}.parquet
  → Zingg Spark link（本目录）
  → data/zingg/raw_matches.jsonl
  → hmd foundation zingg-run --mode export-only
  → ontology/mappings/zingg_matches.jsonl
```

## 配置（`HMD_ZINGG_*`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HMD_ZINGG_MIN_SCORE` | `0.8` | export / Resolver 生效阈值 |
| `HMD_ZINGG_WINDOW_DAYS` | `30` | 扫 `er_observations` 窗口 |
| `HMD_ZINGG_MIN_OCCURRENCES` | `1` | 物化最低频次 |
| `HMD_ZINGG_OBSERVATIONS` | `all` | `lake` / `bootstrap` / `all` |
| `HMD_ZINGG_SKIP_DOCKER` | `false` | `full` 模式跳过容器 |

见仓库根 `.env.example` 与 `biomed_ontology.config.Settings`。

## 快速联调（无 Spark）

```bash
uv run hmd foundation zingg-run --mode stub-link --observations bootstrap
# 或：export HMD_ZINGG_OBSERVATIONS=bootstrap HMD_ZINGG_SKIP_DOCKER=true
#     uv run hmd foundation zingg-run --mode full
```

`stub-link` 读取 `data/zingg/bootstrap_pairs.jsonl` 写出 raw matches 再 export；**不替代**真实 Zingg 模型。

## Spark link（骨架）

```bash
docker compose -f docker/zingg/docker-compose.yml --profile zingg run --rm zingg-link
```

`scripts/link.sh` 为占位：生产需挂载 Zingg jars 并按 `config/link.json` 跑 findTrainingData → label → train → link。详见 [Zingg docs](https://github.com/zinggAI/zingg)。

## 边界

- 只 link 到已有 `HMD:ENT:*`，不 mint
- matches 进 Git 审阅后再生效
- dictionary exact 优先于 zingg（Resolver cascade）
