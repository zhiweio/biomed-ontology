#!/usr/bin/env bash
# macOS Apple Silicon 原生启动（MPS）；Linux 无 Docker 时也可用于 CPU/CUDA。
# 用法：task foundation:up:bern2  （Darwin 且 BERN2_RUNTIME=native）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="${ROOT}/upstream"
RESOURCES="${BERN2_RESOURCES:-${ROOT}/resources}"
VENV="${BERN2_VENV:-${ROOT}/.venv-native}"

if [ ! -f "${UPSTREAM}/server.py" ]; then
  echo "缺少上游源码。请先：task foundation:bern2:fetch" >&2
  exit 2
fi
if [ ! -d "${RESOURCES}" ] || [ -z "$(ls -A "${RESOURCES}" 2>/dev/null || true)" ]; then
  echo "缺少 resources。请先：task foundation:bern2:fetch" >&2
  exit 2
fi

# 把 resources 挂到 upstream 期望路径
if [ ! -e "${UPSTREAM}/resources" ]; then
  ln -sfn "${RESOURCES}" "${UPSTREAM}/resources"
elif [ -L "${UPSTREAM}/resources" ]; then
  ln -sfn "${RESOURCES}" "${UPSTREAM}/resources"
fi

if [ ! -d "${VENV}" ]; then
  echo "Creating native venv → ${VENV}"
  python3 -m venv "${VENV}"
  # shellcheck disable=SC1091
  source "${VENV}/bin/activate"
  pip install -U pip setuptools wheel
  if [[ "$(uname -s)" == "Darwin" ]]; then
    pip install "torch==2.1.2"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    pip install "torch==2.1.2" --index-url "https://download.pytorch.org/whl/cu118"
  else
    pip install "torch==2.1.2" --index-url "https://download.pytorch.org/whl/cpu"
  fi
  grep -viE '^(faiss|torch)([=<>]|$)' "${UPSTREAM}/requirements.txt" > /tmp/bern2-native-reqs.txt
  pip install -r /tmp/bern2-native-reqs.txt "faiss-cpu>=1.7.4,<1.9"
else
  # shellcheck disable=SC1091
  source "${VENV}/bin/activate"
fi

# 幂等打补丁（upstream 在 gitignore，补丁可重复）
python "${ROOT}/hmd/apply_patches.py" "${UPSTREAM}"

export PYTHONPATH="${ROOT}:${UPSTREAM}:${PYTHONPATH:-}"
export BERN2_DEVICE="${BERN2_DEVICE:-auto}"
export BERN2_ACCEL="${BERN2_ACCEL:-native}"

cd "${UPSTREAM}"
# 复用容器 entrypoint 逻辑（路径已对齐）
export BERN2_JAVA_XMX_GNORM="${BERN2_JAVA_XMX_GNORM:-4G}"
export BERN2_JAVA_XMS_GNORM="${BERN2_JAVA_XMS_GNORM:-2G}"
export BERN2_JAVA_XMX_TMVAR="${BERN2_JAVA_XMX_TMVAR:-2G}"
export BERN2_JAVA_XMS_TMVAR="${BERN2_JAVA_XMS_TMVAR:-1G}"

# entrypoint 期望 /app；这里直接内联等价启动
mkdir -p logs multi_ner/tmp multi_ner/input multi_ner/output

python - <<'PY'
from hmd.device import device_name
import os
print(f"BERN2 native device → {device_name()} (BERN2_DEVICE={os.environ.get('BERN2_DEVICE')})")
PY

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

for _ in $(seq 1 90); do
  if python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',18894)); s.close()" 2>/dev/null; then
    break
  fi
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
