#!/usr/bin/env bash
# Start Overseer local node.
# Set GATEWAY_URL + NODE_INFERENCE_URL to register with the VPS gateway.
#
# Example:
#   GATEWAY_URL=https://overseer.yourdomain.com \
#   NODE_INFERENCE_URL=http://100.x.x.x:7860 \
#   NODE_SECRET=your-secret \
#   ./start.sh

set -e
cd "$(dirname "$0")"

export VAULT_PATH="${VAULT_PATH:-$HOME/vault}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-dolphin3:latest}"
export USER_TIMEZONE="${USER_TIMEZONE:-America/Los_Angeles}"
export NODE_PORT="${NODE_PORT:-7860}"

# Load secrets if present
[ -f "$HOME/.secrets/overseer.env" ] && source "$HOME/.secrets/overseer.env"

echo "Starting Overseer node on :$NODE_PORT"
echo "  vault:  $VAULT_PATH"
echo "  model:  $OLLAMA_MODEL @ $OLLAMA_URL"
echo "  gateway: ${GATEWAY_URL:-none}"

exec uvicorn main:app --host 0.0.0.0 --port "$NODE_PORT" --reload
