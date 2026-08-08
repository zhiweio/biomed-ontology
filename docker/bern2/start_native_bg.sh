#!/usr/bin/env bash
# 后台启动并正确写入 pid（避免 Taskfile 吞掉 $!）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "${ROOT}/upstream/logs"
nohup "${ROOT}/run_native.sh" > "${ROOT}/upstream/logs/native_stdout.log" 2>&1 &
echo $! > "${ROOT}/.bern2.pid"
echo "BERN2 native pid=$(cat "${ROOT}/.bern2.pid")"
