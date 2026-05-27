#!/usr/bin/env bash
# Rebuild and restart the overseer container on the VPS.
# Usage: rebuild-vps.sh [commit-hash]
# Default: uses current HEAD commit.
set -e

COMMIT=${1:-$(git -C "$(dirname "$0")/.." rev-parse --short HEAD)}
APP_DIR=/data/coolify/applications/lhqks3whe00kfb9tvrb6ocgh
IMAGE="lhqks3whe00kfb9tvrb6ocgh_overseer:${COMMIT}"

source ~/.secrets/master.env 2>/dev/null || true
VPS=${OVERSEER_SSH_HOST:-}

if [[ -z "$VPS" ]]; then
  echo "error: set OVERSEER_SSH_HOST to the deployment target, e.g. root@your-tailscale-host" >&2
  exit 1
fi

echo "→ building ${IMAGE} on VPS at ${VPS}"
ssh -o StrictHostKeyChecking=no "${VPS}" "
  set -e
  source ${APP_DIR}/.env
  rm -rf /tmp/osgw
  git clone --depth=1 https://\${GIT_USER}:\${GIT_TOKEN}@github.com/samhcus/overseer.git /tmp/osgw
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

echo "→ waiting for gateway smoke..."
sleep 5
if [[ -z "${OVERSEER_API_URL:-}" ]]; then
  echo "error: set OVERSEER_API_URL to the deployed gateway URL before running rebuild-vps.sh" >&2
  exit 1
fi
"$(dirname "$0")/smoke-gateway.sh" "${OVERSEER_API_URL}"
