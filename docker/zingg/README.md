# Zingg batch link

离线模糊 ER：**不在** resolve 请求路径跑 Spark。使用官方镜像 [`zingg/zingg`](https://github.com/zinggAI/zingg)（见 [Linking docs](https://docs.zingg.ai/latest/stepbystep/link.md)）。

```text
ontology SSOT + Iceberg er_observations
  → hmd foundation zingg-run --mode materialize-only
  → data/zingg/input/{enterprise,observation}.parquet
  → data/zingg/training.csv          # bootstrap → trainingSamples
  → docker zingg/zingg: train → link
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

Compose 环境变量：`ZINGG_PHASE=train-link|train|link`（默认 `train-link`）。

## 快速联调（无 Spark）

```bash
uv run hmd foundation zingg-run --mode stub-link --observations bootstrap
```

`stub-link` 读 `bootstrap_pairs.jsonl`；**不替代**真实 Zingg 模型。

## 官方 Docker link

```bash
# 1) 物化 parquet + training.csv
uv run hmd foundation zingg-run --mode materialize-only --observations bootstrap

# 2) 官方镜像 train（trainingSamples）→ link
docker compose -f docker/zingg/docker-compose.yml --profile zingg run --rm zingg-link
# 或：uv run hmd foundation zingg-run --mode full --observations bootstrap

# 3) 导出到 ontology mappings
uv run hmd foundation zingg-run --mode export-only
```

要点（相对旧骨架的修复）：

- 镜像是 **`zingg/zingg:0.6.0`**，不是裸 `apache/spark`（见 [zinggAI/zingg](https://github.com/zinggAI/zingg)）
- 容器 `working_dir` 必须是 `ZINGG_HOME`（`/zingg-0.6.0`），否则 `log4j2.properties` 相对路径失败
- `config/link.json` 对齐官方 [`configLink.json`](https://github.com/zinggAI/zingg/blob/main/examples/febrl/configLink.json)：顶层 `data` / `output` / `fieldDefinition`
- 两侧 pipe 同 schema：`id` / `label` / `kind`（`kind` 为 `dont_use`）
- 用 [`trainingSamples`](https://docs.zingg.ai/latest/stepbystep/createtrainingdata/addowntrainingdata.md) 自 bootstrap 冷启动 `train`，再 `link`（正例需约 30+）
- `scripts/link.sh` 调用 `zingg.sh --phase …`；CSV 列含 `z_zsource`，转成 `raw_matches.jsonl`
- CLI `full` 模式需 `--profile zingg`（已内置）

首次拉镜像约 1.6GB（arm64）。模型落在 `data/zingg/models/1/`（gitignore）。

导出到 `ontology/mappings/zingg_matches.jsonl` 后请人工审阅再提交；合成变体不应整表入库。

## 边界

- 只 link 到已有 `HMD:ENT:*`，不 mint
- matches 进 Git 审阅后再生效
- dictionary exact 优先于 zingg（Resolver cascade）
