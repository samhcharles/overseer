#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${1:-${OVERSEER_API_URL:-}}
API_KEY=${OVERSEER_API_KEY:-}

if [[ -z "$BASE_URL" ]]; then
  echo "error: set OVERSEER_API_URL or pass the gateway URL as the first argument" >&2
  exit 1
fi

BASE_URL=${BASE_URL%/}

echo "→ checking ${BASE_URL}/ready"
curl -fsS --max-time 20 "${BASE_URL}/ready" >/dev/null

echo "→ checking ${BASE_URL}/health"
HEALTH_JSON=$(curl -fsS --max-time 20 "${BASE_URL}/health")
python3 - <<'PY' "$HEALTH_JSON"
import json, sys
data = json.loads(sys.argv[1])
backend = data.get("backend")
status = data.get("backend_status")
trusted_nodes = data.get("trusted_node_count", 0)
print(f"ok  backend={backend}  status={status}  trusted_nodes={trusted_nodes}")
PY

if [[ -n "$API_KEY" ]]; then
  echo "→ checking auth gate on /nodes"
  NOAUTH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 "${BASE_URL}/nodes")
  if [[ "$NOAUTH_CODE" != "401" ]]; then
    echo "error: expected /nodes without auth to return 401, got ${NOAUTH_CODE}" >&2
    exit 1
  fi

  AUTH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
    -H "Authorization: Bearer ${API_KEY}" \
    "${BASE_URL}/nodes")
  if [[ "$AUTH_CODE" != "200" ]]; then
    echo "error: expected /nodes with auth to return 200, got ${AUTH_CODE}" >&2
    exit 1
  fi
fi

echo "→ smoke passed"
