#!/usr/bin/env bash
set -euo pipefail

VAULT_PATH="${VAULT_PATH:-$HOME/vault}"
OVERSEER_API_URL="${OVERSEER_API_URL:-}"
OVERSEER_API_KEY="${OVERSEER_API_KEY:-}"

if [[ -z "$OVERSEER_API_URL" ]]; then
    echo "[raw-watcher] OVERSEER_API_URL not set" >&2
    exit 1
fi

RAW_SESSIONS="${VAULT_PATH}/raw/sessions"

echo "[raw-watcher] watching ${RAW_SESSIONS}"

inotifywait -m -e create "${RAW_SESSIONS}" 2>/dev/null | while read -r _dir _event file; do
    [[ "$file" == *.md ]] || continue
    echo "[raw-watcher] new session: $file — triggering /process-raw"
    curl -s -X POST "${OVERSEER_API_URL}/process-raw" \
        -H "Authorization: Bearer ${OVERSEER_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"max_sessions": 5}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'[raw-watcher] processed={d.get(\"processed\",0)} skipped={d.get(\"skipped\",0)}')" 2>/dev/null || true
done
