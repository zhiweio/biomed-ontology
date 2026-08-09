#!/usr/bin/env bash
# 后台启动并正确写入 pid（避免 Taskfile / 父 shell 退出带走进程组）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "${ROOT}/upstream/logs"
PID_FILE="${ROOT}/.bern2.pid"

# 双 fork：脱离调用方 process group（macOS 无 setsid）
python3 - "$ROOT" <<'PY'
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
script = root / "run_native.sh"
log = root / "upstream" / "logs" / "native_stdout.log"
pid_file = root / ".bern2.pid"
env = os.environ.copy()

if os.fork() > 0:
    raise SystemExit(0)
os.setsid()
if os.fork() > 0:
    raise SystemExit(0)

os.chdir(str(root))
fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
os.close(fd)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.close(devnull)

proc = subprocess.Popen(["bash", str(script)], env=env, start_new_session=True)
pid_file.write_text(str(proc.pid))
raise SystemExit(0)
PY

sleep 0.3
echo "BERN2 native pid=$(cat "${PID_FILE}")"
