#!/usr/bin/env bash
# Register Iceberg Sink connectors against Connect REST (localhost:8083).
set -euo pipefail

CONNECT_URL="${CONNECT_URL:-http://127.0.0.1:8083}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

wait_connect() {
  local i
  for i in $(seq 1 60); do
    if curl -fsS "${CONNECT_URL}/connectors" >/dev/null 2>&1; then
      echo "connect ready: ${CONNECT_URL}"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: Connect REST not ready at ${CONNECT_URL}" >&2
  return 1
}

register() {
  local name="$1"
  local file="$2"
  echo "register ${name} <- ${file}"
  curl -fsS -X PUT \
    -H 'Content-Type: application/json' \
    --data @"${file}" \
    "${CONNECT_URL}/connectors/${name}/config" | tee "/tmp/${name}.json"
  echo
}

wait_connect
register "hmd-er-observations" "${ROOT}/connectors/er_observations.json"
register "hmd-obs-tool-io" "${ROOT}/connectors/obs_tool_io.json"

echo "status:"
curl -fsS "${CONNECT_URL}/connectors?expand=status" | python3 -m json.tool 2>/dev/null \
  || curl -fsS "${CONNECT_URL}/connectors"
