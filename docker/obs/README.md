# Observability → Redpanda → Iceberg

热路径只 **Kafka-API produce**（`ObsEventProducer`）；入湖由 **Iceberg Kafka Connect Sink** 批量提交（PoC 约 15s），禁止 App 同步 `table.append`。

## 启动

```bash
# 需已有 MinIO + iceberg-rest（foundation/lake）
task obs:up          # Redpanda :19092 + Connect :8083（自建镜像含 Iceberg 1.9.2 插件）
task obs:register    # PUT connector configs
uv run hmd lake init # 确保 hmd.obs_tool_io / hmd.er_observations 存在
```

Connect worker 镜像：`docker/obs/Dockerfile.connect`（`confluentinc/cp-kafka-connect` + [Confluent Hub Iceberg Sink 1.9.2](https://www.confluent.io/hub/iceberg/iceberg-kafka-connect)）。无现成 all-in-one 公共镜像。

App 环境变量（`Settings` / `.env`，完整列表见仓库根 `.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HMD_OBS_EVENTS_ENABLED` | `true` | `false` 关闭 produce/WAL |
| `HMD_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:19092` | 默认 Redpanda；设空=仅 WAL |
| `HMD_KAFKA_OBS_TOOL_IO_TOPIC` | `hmd.obs.tool_io` | → Iceberg `hmd.obs_tool_io` |
| `HMD_KAFKA_ER_OBSERVATIONS_TOPIC` | `hmd.er.observations` | → Iceberg `hmd.er_observations` |
| `HMD_OBS_WAL_DIR` | `data/obs_wal` | broker 不可达时的 Jsonl WAL |

Producer 在 `emit_*` 结束与 `atexit` 时 `flush`，避免短 CLI 进程丢消息。

## 注册 Iceberg Sink connector

```bash
task obs:register
# 或：
bash docker/obs/scripts/register_connectors.sh
```

配置见 `docker/obs/connectors/*.json`（REST catalog + MinIO，与 `docker-compose.lake.yml` 一致）。小文件维护：用 Spark/Trino 周期 compaction（Connect 不做）。

## 验收

```bash
# produce
uv run python -c '
from biomed_ontology.lake import obs_events
from biomed_ontology.config import Settings
obs_events._producer = None
cfg = Settings()
obs_events.emit_er_observation(mention="connect-e2e", source="runtime_resolve", cfg=cfg)
'
# 等 commit（~15s）后扫表
uv run python -c '
from biomed_ontology.lake.catalog import ER_OBSERVATIONS_TABLE, open_catalog
t = open_catalog().load_table(ER_OBSERVATIONS_TABLE)
print("rows", t.scan().to_arrow().num_rows)
'
```

## 不在范围

- OpenTelemetry SDK + Collector（P2）
- 自研 ObsShipper / 热路径 PyIceberg append
