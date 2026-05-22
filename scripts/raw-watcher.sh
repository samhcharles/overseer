#!/usr/bin/env bash
set -euo pipefail

VAULT_PATH="${VAULT_PATH:-$HOME/vault}"
OVERSEER_API_URL="${OVERSEER_API_URL:-}"
OVERSEER_API_KEY="${OVERSEER_API_KEY:-}"
PROCESS_BATCH_SIZE="${PROCESS_BATCH_SIZE:-5}"
RECONCILE_INTERVAL="${RECONCILE_INTERVAL:-300}"

if [[ -z "$OVERSEER_API_URL" ]]; then
    echo "[raw-watcher] OVERSEER_API_URL not set" >&2
    exit 1
fi

RAW_SESSIONS="${VAULT_PATH}/raw/sessions"
AUTH_HEADER=()
if [[ -n "$OVERSEER_API_KEY" ]]; then
    AUTH_HEADER=(-H "Authorization: Bearer ${OVERSEER_API_KEY}")
fi

wait_for_raw_dir() {
    until [[ -d "$RAW_SESSIONS" ]]; do
        echo "[raw-watcher] waiting for ${RAW_SESSIONS}"
        sleep 5
    done
}

wait_for_overseer() {
    until curl -fsS -m 5 "${OVERSEER_API_URL}/health" "${AUTH_HEADER[@]}" >/dev/null; do
        echo "[raw-watcher] waiting for Overseer health at ${OVERSEER_API_URL}/health"
        sleep 5
    done
}

trigger_process() {
    local reason="${1:-manual}"
    echo "[raw-watcher] trigger=${reason}"
    if ! curl -fsS -X POST "${OVERSEER_API_URL}/process-raw" \
        "${AUTH_HEADER[@]}" \
        -H "Content-Type: application/json" \
        -d "{\"max_sessions\": ${PROCESS_BATCH_SIZE}}" | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(f'[raw-watcher] processed={d.get(\"processed\",0)} skipped={d.get(\"skipped\",0)}')" ; then
        echo "[raw-watcher] process-raw failed for trigger=${reason}" >&2
        return 1
    fi
}

reconcile_loop() {
    while true; do
        sleep "${RECONCILE_INTERVAL}"
        trigger_process "reconcile" || true
    done
}

watch_loop() {
    while true; do
        inotifywait -m -e create -e close_write -e moved_to --format '%f' "${RAW_SESSIONS}" 2>/dev/null | while read -r file; do
            [[ "$file" == *.md ]] || continue
            trigger_process "fs:${file}" || true
        done
        echo "[raw-watcher] inotify stream ended, restarting watcher" >&2
        sleep 2
    done
}

wait_for_raw_dir
wait_for_overseer

echo "[raw-watcher] watching ${RAW_SESSIONS}"
trigger_process "startup" || true
reconcile_loop &
watch_loop
