#!/usr/bin/env bash
# Rebuild and restart the overseer container on the VPS.
# Usage: rebuild-vps.sh [commit-hash]
# Default: uses current HEAD commit.
set -e

COMMIT=${1:-$(git -C "$(dirname "$0")/.." rev-parse --short HEAD)}
APP_DIR=/data/coolify/applications/lhqks3whe00kfb9tvrb6ocgh
IMAGE="lhqks3whe00kfb9tvrb6ocgh_overseer:${COMMIT}"
VPS=root@100.73.12.59

source ~/.secrets/master.env 2>/dev/null || true

echo "→ building ${IMAGE} on VPS at ${VPS}"
ssh -o StrictHostKeyChecking=no "${VPS}" "
  set -e
  source ${APP_DIR}/.env
  rm -rf /tmp/osgw
  git clone --depth=1 https://\${GIT_USER}:\${GIT_TOKEN}@github.com/samhcharles/overseer.git /tmp/osgw
  docker build -t ${IMAGE} /tmp/osgw/api
  python3 -c \"
import yaml
path = '${APP_DIR}/docker-compose.yaml'
with open(path) as f: c = yaml.safe_load(f)
c['services']['overseer']['image'] = '${IMAGE}'
c['services']['overseer'].pop('build', None)
with open(path, 'w') as f: yaml.dump(c, f, default_flow_style=False)
print('image set to ${IMAGE}')
\"
  cd ${APP_DIR}
  docker compose -f docker-compose.yaml --env-file .env up -d --no-deps overseer
  echo 'restarted'
  docker ps --filter name=overseer-lhqks --format 'Image: {{.Image}}  Status: {{.Status}}'
"

echo "→ waiting for /health..."
sleep 5
curl -sf "${OVERSEER_API_URL:-http://100.73.12.59:8765}/health" \
  -H "Authorization: Bearer ${OVERSEER_API_KEY:-}" | \
  python3 -c "
import json,sys
d=json.load(sys.stdin)
active=d.get('active_slots',[])
blocked=d.get('blocked_slots',{})
print(f'ok  backend={d[\"backend\"]}  slots={len(active)}/{len(active)+len(blocked)}  status={d[\"backend_status\"]}')
"
