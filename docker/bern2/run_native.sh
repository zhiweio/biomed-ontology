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

# 优先 3.11/3.12：系统 python3 若是 3.14 则无 torch==2.1.2 轮子
PYBIN="${BERN2_PYTHON:-}"
if [ -z "${PYBIN}" ]; then
  for c in python3.11 python3.12 python3.10 python3; do
    if command -v "${c}" >/dev/null 2>&1; then
      PYBIN="${c}"
      break
    fi
  done
fi
PYBIN="${PYBIN:-python3}"

install_native_deps() {
  pip install -U pip setuptools wheel
  if [[ "$(uname -s)" == "Darwin" ]]; then
    pip install "torch==2.1.2" || pip install "torch>=2.2,<2.7"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    pip install "torch==2.1.2" --index-url "https://download.pytorch.org/whl/cu118" \
      || pip install "torch>=2.2,<2.7" --index-url "https://download.pytorch.org/whl/cu118"
  else
    pip install "torch==2.1.2" --index-url "https://download.pytorch.org/whl/cpu" \
      || pip install "torch>=2.2,<2.7" --index-url "https://download.pytorch.org/whl/cpu"
  fi
  # 上游 requirements 过旧（numpy 1.19 / transformers 4.9）；原生环境用兼容集
  pip install \
    "numpy>=1.23,<1.27" \
    "pandas>=1.5,<2.3" \
    "requests>=2.28" \
    "xmltodict>=0.13" \
    "tqdm>=4.66" \
    "transformers>=4.30,<4.45" \
    "accelerate>=0.20,<0.34" \
    "Flask>=2.3,<3.1" \
    "bioregistry>=0.10" \
    "pymongo>=4.0" \
    "faiss-cpu>=1.7.4,<1.9"
}

if [ ! -d "${VENV}" ] || [ ! -x "${VENV}/bin/python" ]; then
  rm -rf "${VENV}"
  echo "Creating native venv → ${VENV} (python=${PYBIN})"
  "${PYBIN}" -m venv "${VENV}"
  # shellcheck disable=SC1091
  source "${VENV}/bin/activate"
  install_native_deps
else
  # shellcheck disable=SC1091
  source "${VENV}/bin/activate"
  if ! python -c "import flask, numpy, transformers, faiss" 2>/dev/null; then
    echo "Native deps incomplete — installing…"
    install_native_deps
  fi
fi

# 幂等打补丁（upstream 在 gitignore，补丁可重复）
python "${ROOT}/hmd/apply_patches.py" "${UPSTREAM}"

export PYTHONPATH="${ROOT}:${UPSTREAM}:${PYTHONPATH:-}"
export BERN2_DEVICE="${BERN2_DEVICE:-auto}"
export BERN2_ACCEL="${BERN2_ACCEL:-native}"

cd "${UPSTREAM}"
export BERN2_JAVA_XMX_GNORM="${BERN2_JAVA_XMX_GNORM:-4G}"
export BERN2_JAVA_XMS_GNORM="${BERN2_JAVA_XMS_GNORM:-2G}"
export BERN2_JAVA_XMX_TMVAR="${BERN2_JAVA_XMX_TMVAR:-2G}"
export BERN2_JAVA_XMS_TMVAR="${BERN2_JAVA_XMS_TMVAR:-1G}"
export BERN2_JAVA_XMX_DISEASE="${BERN2_JAVA_XMX_DISEASE:-6G}"
export BERN2_JAVA_XMX_GENE="${BERN2_JAVA_XMX_GENE:-8G}"

LOG_DIR="${UPSTREAM}/logs"
mkdir -p "${LOG_DIR}" multi_ner/tmp multi_ner/input multi_ner/output \
  resources/normalization/inputs/disease resources/normalization/outputs/disease

# GNormPlus 调用 ./CRF/crf_test；资源包通常只有源码，需本机构建
CRF_DIR="${ROOT}/resources/GNormPlusJava/CRF"
if [ -d "${CRF_DIR}" ] && [ ! -x "${CRF_DIR}/.libs/crf_test" ]; then
  echo "Building CRF++ for GNormPlus → ${CRF_DIR}"
  (cd "${CRF_DIR}" && ./configure && make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 2)")
fi
if [ -d "${CRF_DIR}" ] && [ ! -x "${CRF_DIR}/.libs/crf_test" ] && [ ! -x "${CRF_DIR}/crf_test" ]; then
  echo "ERROR: CRF++ 未编译成功（缺少 crf_test）。GNormPlus 无法运行。" >&2
  exit 1
fi

python - <<'PY'
from hmd.device import device_name
import os
print(f"BERN2 native device → {device_name()} (BERN2_DEVICE={os.environ.get('BERN2_DEVICE')})")
PY

: > "${LOG_DIR}/nohup_multi_ner.out"
: > "${LOG_DIR}/nohup_gnormplus.out"
: > "${LOG_DIR}/nohup_tmvar.out"
: > "${LOG_DIR}/nohup_disease_normalize.out"
: > "${LOG_DIR}/nohup_gene_normalize.out"

python multi_ner/ner_server.py --mtner_home multi_ner --mtner_port 18894 \
  >> "${LOG_DIR}/nohup_multi_ner.out" 2>&1 &

if [ -f resources/GNormPlusJava/GNormPlusServer.main.jar ]; then
  (cd resources/GNormPlusJava && java \
    "-Xmx${BERN2_JAVA_XMX_GNORM}" "-Xms${BERN2_JAVA_XMS_GNORM}" \
    -jar GNormPlusServer.main.jar 18895 \
    >> "${LOG_DIR}/nohup_gnormplus.out" 2>&1 &)
fi
if [ -f resources/tmVarJava/tmVar2Server.main.jar ]; then
  (cd resources/tmVarJava && java \
    "-Xmx${BERN2_JAVA_XMX_TMVAR}" "-Xms${BERN2_JAVA_XMS_TMVAR}" \
    -jar tmVar2Server.main.jar 18896 \
    >> "${LOG_DIR}/nohup_tmvar.out" 2>&1 &)
fi

# Rule-based disease/gene normalizers（官方 run_bern2.sh；缺了会导致 normalize IndexError）
if [ -f resources/normalization/normalizers/disease/disease_normalizer_21.jar ]; then
  (cd resources/normalization && java \
    "-Xmx${BERN2_JAVA_XMX_DISEASE}" \
    -jar normalizers/disease/disease_normalizer_21.jar \
    "inputs/disease" \
    "outputs/disease" \
    "dictionary/dict_Disease_20210630.txt" \
    "normalizers/disease/resources" \
    9 \
    18892 \
    >> "${LOG_DIR}/nohup_disease_normalize.out" 2>&1 &)
fi
if [ -f resources/normalization/normalizers/gene/gnormplus-normalization_21.jar ]; then
  (cd resources/normalization/normalizers/gene && java \
    "-Xmx${BERN2_JAVA_XMX_GENE}" \
    -jar gnormplus-normalization_21.jar \
    18888 \
    >> "${LOG_DIR}/nohup_gene_normalize.out" 2>&1 &)
fi

# 切勿对 18894/18895 做空 TCP connect：NER/GNorm 协议一连上就读 payload，会直接崩服
port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

ner_up=0
for _ in $(seq 1 120); do
  if port_listening 18894; then
    echo "multi_ner listening on 18894"
    ner_up=1
    break
  fi
  sleep 2
done
if [ "${ner_up}" != "1" ]; then
  echo "ERROR: multi_ner 未在 18894 监听。见 ${LOG_DIR}/nohup_multi_ner.out" >&2
  tail -40 "${LOG_DIR}/nohup_multi_ner.out" >&2 || true
  exit 1
fi
gnorm_up=0
for _ in $(seq 1 90); do
  if port_listening 18895; then
    echo "GNormPlus listening on 18895"
    gnorm_up=1
    break
  fi
  sleep 1
done
if [ "${gnorm_up}" != "1" ]; then
  echo "WARN: GNormPlus 未在 18895 监听。见 ${LOG_DIR}/nohup_gnormplus.out" >&2
  tail -40 "${LOG_DIR}/nohup_gnormplus.out" >&2 || true
fi
for port in 18888 18892; do
  up=0
  for _ in $(seq 1 120); do
    if port_listening "${port}"; then
      echo "normalizer listening on ${port}"
      up=1
      break
    fi
    sleep 2
  done
  if [ "${up}" != "1" ]; then
    echo "WARN: normalizer 未在 ${port} 监听（gene=18888 disease=18892）" >&2
    tail -20 "${LOG_DIR}/nohup_gene_normalize.out" >&2 || true
    tail -20 "${LOG_DIR}/nohup_disease_normalize.out" >&2 || true
  fi
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
