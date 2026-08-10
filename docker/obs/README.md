# Observability → Redpanda → Iceberg

热路径只 **Kafka-API produce**（`ObsEventProducer`）；入湖由 **Iceberg Kafka Connect Sink** 批量提交（默认约 5–10 分钟），禁止 App 同步 `table.append`。

## 启动

```bash
# 需已有 MinIO + iceberg-rest（foundation/lake）
docker compose -f docker/docker-compose.foundation.yml \
  -f docker/docker-compose.obs.yml --profile obs up -d redpanda

# 可选：Connect worker（镜像/插件以环境为准，见下方注册 connector）
docker compose -f docker/docker-compose.foundation.yml \
  -f docker/docker-compose.obs.yml --profile obs up -d
```

App 环境变量（`Settings` / `.env`，完整列表见仓库根 `.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HMD_OBS_EVENTS_ENABLED` | `true` | `false` 关闭 produce/WAL |
| `HMD_KAFKA_BOOTSTRAP_SERVERS` | `localhost:19092` | 默认 Redpanda；设空=仅 WAL |
| `HMD_KAFKA_OBS_TOOL_IO_TOPIC` | `hmd.obs.tool_io` | → Iceberg `hmd.obs_tool_io` |
| `HMD_KAFKA_ER_OBSERVATIONS_TOPIC` | `hmd.er.observations` | → Iceberg `hmd.er_observations` |
| `HMD_OBS_WAL_DIR` | `data/obs_wal` | broker 不可达时的 Jsonl WAL |

```bash
task obs:up             # 启动 Redpanda（与 Settings 默认一致）
uv run hmd lake init    # 创建含观测表在内的 Iceberg 表
```

## 注册 Iceberg Sink connector

示例（Connect REST 在 `localhost:8083`）：

```bash
curl -s -X PUT -H 'Content-Type: application/json' \
  --data @docker/obs/connectors/er_observations.json \
  http://localhost:8083/connectors/hmd-er-observations/config

curl -s -X PUT -H 'Content-Type: application/json' \
  --data @docker/obs/connectors/obs_tool_io.json \
  http://localhost:8083/connectors/hmd-obs-tool-io/config
```

配置需指向本仓 REST catalog + MinIO（与 `docker-compose.lake.yml` 一致）。小文件维护：用 Spark/Trino 周期 compaction（Connect 不做）。

## 不在范围

- OpenTelemetry SDK + Collector（P2）
- 自研 ObsShipper / 热路径 PyIceberg append
