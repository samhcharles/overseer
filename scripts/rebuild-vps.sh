#!/usr/bin/env bash
# Rebuild and restart the overseer container on the VPS.
# Usage: rebuild-vps.sh [commit-hash]
# Default: uses current HEAD commit.
set -e

COMMIT=${1:-$(git -C "$(dirname "$0")/.." rev-parse --short HEAD)}
APP_DIR=/data/coolify/applications/lhqks3whe00kfb9tvrb6ocgh
IMAGE="lhqks3whe00kfb9tvrb6ocgh_overseer:${COMMIT}"
VPS=root@100.73.12.59

source ~/.secrets/master.env 2>/dev/null

echo "→ building ${IMAGE} on VPS at ${VPS}"
ssh -o StrictHostKeyChecking=no "${VPS}" "
  set -e
  source ${APP_DIR}/.env
  rm -rf /tmp/osgw
  git clone --depth=1 https://\${GIT_USER}:\${GIT_TOKEN}@github.com/samhcharles/overseer.git /tmp/osgw
  docker build -t ${IMAGE} /tmp/osgw/api
  sed -i \"s|image: '.*overseer:.*'|image: '${IMAGE}'|\" ${APP_DIR}/docker-compose.yaml
  cd ${APP_DIR}
  docker compose -f docker-compose.yaml --env-file .env up -d --no-deps overseer
  echo 'restarted'
  docker ps --filter name=overseer-lhqks --format 'Image: {{.Image}}  Status: {{.Status}}'
"

echo "→ waiting for /health..."
sleep 5
curl -sf "http://100.73.12.59:8765/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'ok  backend={d[\"backend\"]}  model={d[\"model\"]}')"
