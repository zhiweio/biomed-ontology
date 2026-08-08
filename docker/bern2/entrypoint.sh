#!/usr/bin/env sh
set -eu

if [ ! -d /app/resources ] || [ -z "$(ls -A /app/resources 2>/dev/null || true)" ]; then
  echo "BERN2 resources 未挂载或为空。"
  echo "请先：task foundation:bern2:fetch"
  echo "或设置 BERN2_RESOURCES 指向已解压的 resources/ 目录。"
  exit 1
fi

if [ ! -f /app/server.py ]; then
  echo "BERN2 源码缺失（期望 /app/server.py）。请先 task foundation:bern2:fetch"
  exit 1
fi

export PYTHONPATH="/opt:${PYTHONPATH:-}"
export BERN2_DEVICE="${BERN2_DEVICE:-auto}"

# Java heaps：CUDA 主机默认偏大；CPU/Apple Silicon Docker 用更保守值
# （compose 可能传入空字符串，不能用 :=）
ACCEL="${BERN2_ACCEL:-cpu}"
if [ "$ACCEL" = "cuda" ]; then
  [ -n "${BERN2_JAVA_XMX_GNORM:-}" ] || BERN2_JAVA_XMX_GNORM=8G
  [ -n "${BERN2_JAVA_XMS_GNORM:-}" ] || BERN2_JAVA_XMS_GNORM=4G
  [ -n "${BERN2_JAVA_XMX_TMVAR:-}" ] || BERN2_JAVA_XMX_TMVAR=4G
  [ -n "${BERN2_JAVA_XMS_TMVAR:-}" ] || BERN2_JAVA_XMS_TMVAR=2G
else
  [ -n "${BERN2_JAVA_XMX_GNORM:-}" ] || BERN2_JAVA_XMX_GNORM=4G
  [ -n "${BERN2_JAVA_XMS_GNORM:-}" ] || BERN2_JAVA_XMS_GNORM=2G
  [ -n "${BERN2_JAVA_XMX_TMVAR:-}" ] || BERN2_JAVA_XMX_TMVAR=2G
  [ -n "${BERN2_JAVA_XMS_TMVAR:-}" ] || BERN2_JAVA_XMS_TMVAR=1G
fi
export BERN2_JAVA_XMX_GNORM BERN2_JAVA_XMS_GNORM BERN2_JAVA_XMX_TMVAR BERN2_JAVA_XMS_TMVAR

python - <<'PY'
import os
try:
    from hmd.device import DEVICE, device_name
except Exception as exc:
    print("WARN: device probe failed:", exc)
else:
    print(f"BERN2 device → {device_name()} (BERN2_DEVICE={os.environ.get('BERN2_DEVICE', 'auto')}, BERN2_ACCEL={os.environ.get('BERN2_ACCEL', '?')})")
    if DEVICE.type == "cpu":
        print("NOTE: CPU 推理极慢。Linux+NVIDIA 请用 BERN2_ACCEL=cuda；macOS 原生 MPS 见 task foundation:up:bern2 (BERN2_RUNTIME=native)")
PY

mkdir -p /app/logs /app/multi_ner/tmp /app/multi_ner/input /app/multi_ner/output
cd /app

python multi_ner/ner_server.py --mtner_home multi_ner --mtner_port 18894 \
  >> logs/nohup_multi_ner.out 2>&1 &

if [ -f resources/GNormPlusJava/GNormPlusServer.main.jar ]; then
  (cd resources/GNormPlusJava && java \
    "-Xmx${BERN2_JAVA_XMX_GNORM}" "-Xms${BERN2_JAVA_XMS_GNORM}" \
    -jar GNormPlusServer.main.jar 18895 \
    >> ../../logs/nohup_gnormplus.out 2>&1 &)
fi
if [ -f resources/tmVarJava/tmVar2Server.main.jar ]; then
  (cd resources/tmVarJava && java \
    "-Xmx${BERN2_JAVA_XMX_TMVAR}" "-Xms${BERN2_JAVA_XMS_TMVAR}" \
    -jar tmVar2Server.main.jar 18896 \
    >> ../../logs/nohup_tmvar.out 2>&1 &)
fi

mkdir -p resources/normalization/inputs/disease resources/normalization/outputs/disease
if [ -f resources/normalization/normalizers/disease/disease_normalizer_21.jar ]; then
  (cd resources/normalization && java \
    "-Xmx${BERN2_JAVA_XMX_DISEASE:-6G}" \
    -jar normalizers/disease/disease_normalizer_21.jar \
    "inputs/disease" \
    "outputs/disease" \
    "dictionary/dict_Disease_20210630.txt" \
    "normalizers/disease/resources" \
    9 \
    18892 \
    >> ../../logs/nohup_disease_normalize.out 2>&1 &)
fi
if [ -f resources/normalization/normalizers/gene/gnormplus-normalization_21.jar ]; then
  (cd resources/normalization/normalizers/gene && java \
    "-Xmx${BERN2_JAVA_XMX_GENE:-8G}" \
    -jar gnormplus-normalization_21.jar \
    18888 \
    >> ../../../../logs/nohup_gene_normalize.out 2>&1 &)
fi

# 等 NER 端口（模型加载可能很久）
# 切勿空 TCP connect：NER/GNorm 一连上就按协议读数据，会崩服
i=0
while [ "$i" -lt 90 ]; do
  if (command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :18894 )" | grep -q 18894) \
    || (command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:18894 -sTCP:LISTEN >/dev/null 2>&1) \
    || (command -v netstat >/dev/null 2>&1 && netstat -lnt 2>/dev/null | grep -q ':18894'); then
    echo "multi_ner listening on 18894"
    break
  fi
  i=$((i + 1))
  sleep 2
done

exec python -u server.py \
  --mtner_home ./multi_ner \
  --mtner_port 18894 \
  --gnormplus_home ./resources/GNormPlusJava \
  --gnormplus_port 18895 \
  --tmvar2_home ./resources/tmVarJava \
  --tmvar2_port 18896 \
  --gene_norm_port 18888 \
  --disease_norm_port 18892 \
  --use_neural_normalizer \
  --port 8888
