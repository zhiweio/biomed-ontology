#!/usr/bin/env bash
# Run official Zingg train/link against materialized parquet.
# Image: zingg/zingg (https://github.com/zinggAI/zingg)
set -euo pipefail

CONF="${ZINGG_CONF:-/home/zingg/config/link.json}"
DATA_ROOT="/home/zingg/data/zingg"
ENT="${DATA_ROOT}/input/enterprise.parquet"
OBS="${DATA_ROOT}/input/observation.parquet"
OUT_DIR="${DATA_ROOT}/raw_zingg"
PHASE="${ZINGG_PHASE:-train-link}"

find_zingg_sh() {
  local c
  for c in \
    /zingg-*/scripts/zingg.sh \
    /home/zingg/scripts/zingg.sh \
    /zingg/scripts/zingg.sh \
    ./scripts/zingg.sh; do
    # shellcheck disable=SC2086
    for hit in $c; do
      if [[ -x "$hit" ]]; then
        echo "$hit"
        return 0
      fi
    done
  done
  return 1
}

ZINGG_SH="$(find_zingg_sh || true)"
if [[ -z "${ZINGG_SH}" ]]; then
  echo "ERROR: zingg.sh not found in image (expect zingg/zingg)" >&2
  exit 1
fi

ZINGG_HOME="$(cd "$(dirname "${ZINGG_SH}")/.." && pwd)"
export ZINGG_HOME
cd "${ZINGG_HOME}"

echo "zingg.sh=${ZINGG_SH}"
echo "ZINGG_HOME=${ZINGG_HOME}"
echo "conf=${CONF} phase=${PHASE}"

if [[ ! -f "${ENT}" || ! -f "${OBS}" ]]; then
  echo "ERROR: missing input parquet. Run: uv run hmd foundation zingg-run --mode materialize-only" >&2
  ls -la "${DATA_ROOT}/input" 2>/dev/null || true
  exit 1
fi
if [[ ! -f "${DATA_ROOT}/training.csv" ]]; then
  echo "ERROR: missing ${DATA_ROOT}/training.csv (written by materialize from bootstrap_pairs)" >&2
  exit 1
fi

mkdir -p "${DATA_ROOT}/models" "${OUT_DIR}"
# 清目录（含 .crc 等点文件；勿用 rm dir/*，会漏掉点文件且易踩 pipefail）
find "${OUT_DIR}" -mindepth 1 -delete 2>/dev/null || true

MODEL_DIR="${DATA_ROOT}/models/1"
run_phase() {
  local p="$1"
  echo ">>> zingg --phase ${p}"
  # zingg.sh 偶发 Spark 失败仍 exit 0；用后续产物校验
  if ! "${ZINGG_SH}" --phase "${p}" --conf "${CONF}"; then
    echo "ERROR: zingg phase ${p} failed" >&2
    exit 1
  fi
}

case "${PHASE}" in
  train)
    run_phase train
    ;;
  link)
    if [[ ! -d "${MODEL_DIR}" ]]; then
      echo "ERROR: model missing at ${MODEL_DIR}; run with ZINGG_PHASE=train-link first" >&2
      exit 1
    fi
    run_phase link
    ;;
  train-link)
    if [[ ! -d "${MODEL_DIR}" ]]; then
      run_phase train
    else
      echo "model exists at ${MODEL_DIR}; skip train"
    fi
    run_phase link
    ;;
  *)
    echo "ERROR: ZINGG_PHASE must be train|link|train-link (got ${PHASE})" >&2
    exit 2
    ;;
esac

if [[ ! -d "${MODEL_DIR}" ]]; then
  echo "ERROR: train did not produce model at ${MODEL_DIR}" >&2
  exit 1
fi
# 用 find -print 计数，避免 pipefail + head/grep -q 的 SIGPIPE(141)
part_count="$(find "${OUT_DIR}" -type f \( -name 'part-*' -o -name '*.csv' \) ! -name '.*' -print | wc -l | tr -d ' ')"
if [[ "${part_count}" -lt 1 ]]; then
  echo "ERROR: link produced no files under ${OUT_DIR}" >&2
  ls -la "${OUT_DIR}" || true
  exit 1
fi

python3 /home/zingg/scripts-hmd/convert_output.py \
  --zingg-out "${OUT_DIR}" \
  --raw-out "${DATA_ROOT}/raw_matches.jsonl" \
  --model-id "1"

echo "DONE raw_matches=${DATA_ROOT}/raw_matches.jsonl parts=${part_count}"
# head 会提前关管道 → ls 收到 SIGPIPE → exit 141；在 pipefail 下必须吞掉
set +o pipefail
ls -la "${OUT_DIR}" | head -20
set -o pipefail
wc -l "${DATA_ROOT}/raw_matches.jsonl" || true
