#!/usr/bin/env bash
# Overseer VPS security audit — runs remotely over SSH, prints a scored report.
# Usage: ./audit-security.sh [vps-host]
set -euo pipefail

VPS=${1:-${OVERSEER_SSH_HOST:-}}
PASS=0; FAIL=0; WARN=0

if [[ -z "$VPS" ]]; then
  echo "Usage: ./audit-security.sh [vps-host] or set OVERSEER_SSH_HOST" >&2
  exit 1
fi

ok()   { echo "  [PASS] $*"; ((PASS++)); }
fail() { echo "  [FAIL] $*"; ((FAIL++)); }
warn() { echo "  [WARN] $*"; ((WARN++)); }

echo "────────────────────────────────────────────────"
echo "  Overseer VPS Security Audit — $(date '+%Y-%m-%d %H:%M')"
echo "  Target: $VPS"
echo "────────────────────────────────────────────────"

ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$VPS" bash <<'REMOTE'
set -euo pipefail

echo ""
echo "── SSH ─────────────────────────────────────────"
PA=$(sshd -T -C user=root,addr=1.2.3.4,host=localhost 2>/dev/null | grep '^passwordauthentication' | awk '{print $2}')
echo "  password_auth: $PA"
RL=$(sshd -T -C user=root,addr=1.2.3.4,host=localhost 2>/dev/null | grep '^permitrootlogin' | awk '{print $2}')
echo "  root_login: $RL"
KEY_COUNT=$(wc -l < ~/.ssh/authorized_keys 2>/dev/null || echo 0)
echo "  authorized_keys: $KEY_COUNT"

echo ""
echo "── Firewall ─────────────────────────────────────"
UFW_STATUS=$(ufw status 2>/dev/null | head -1 | awk '{print $2}')
echo "  ufw: $UFW_STATUS"

echo ""
echo "── Port exposure (public internet) ─────────────"
ss -tlnp | grep LISTEN | while read -r _ _ _ addr _ proc; do
  port=$(echo "$addr" | sed 's/.*://')
  host=$(echo "$addr" | cut -d: -f1)
  pname=$(echo "$proc" | grep -oP 'users:\(\("([^"]+)"' | head -1 | sed 's/users:(("//')
  case "$host" in
    0.0.0.0|\:\:) echo "  PUBLIC  :$port  [$pname]" ;;
    100.*) echo "  tailscale :$port  [$pname]" ;;
    127.*|::1) echo "  loopback  :$port  [$pname]" ;;
    *) echo "  other($host) :$port  [$pname]" ;;
  esac
done

echo ""
echo "── Docker exposure ──────────────────────────────"
docker ps --format '{{.Names}}\t{{.Ports}}' | while IFS=$'\t' read -r name ports; do
  [[ -z "$ports" ]] && continue
  echo "  $name → $ports"
done

echo ""
echo "── DOCKER-USER chain (8765 rule) ────────────────"
iptables -L DOCKER-USER -n 2>/dev/null | grep 8765 || echo "  (no 8765 rule in DOCKER-USER)"

echo ""
echo "── Fail2ban ─────────────────────────────────────"
systemctl is-active fail2ban 2>/dev/null && echo "  fail2ban: active" || echo "  fail2ban: INACTIVE"
fail2ban-client status 2>/dev/null | grep "Jail list" || true

echo ""
echo "── Overseer API auth ────────────────────────────"
OVERSEER_CONTAINER=$(docker ps --filter 'name=overseer-lhqks' --format '{{.Names}}' | head -1)
if [ -n "$OVERSEER_CONTAINER" ]; then
  CODE=$(docker exec "$OVERSEER_CONTAINER" python - <<'PY'
import urllib.request
req = urllib.request.Request(
    'http://127.0.0.1:8765/chat',
    data=b'{"message":"ping"}',
    headers={'Content-Type': 'application/json'},
    method='POST',
)
try:
    urllib.request.urlopen(req, timeout=5)
    print('200')
except Exception as exc:
    status = getattr(exc, 'code', None)
    print(status or 'conn-failed')
PY
)
  HEALTH=$(docker exec "$OVERSEER_CONTAINER" python - <<'PY'
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=5) as response:
        print(response.getcode())
except Exception as exc:
    status = getattr(exc, 'code', None)
    print(status or 'conn-failed')
PY
)
else
  CODE="container-missing"
  HEALTH="container-missing"
fi
echo "  /chat no-auth → HTTP ${CODE:-conn-failed}  (want 401)"
echo "  /health no-auth → HTTP ${HEALTH:-conn-failed}  (want 200)"

echo ""
echo "── Secrets in .env (names only) ─────────────────"
grep -o '^[A-Z_]*' /data/coolify/applications/lhqks3whe00kfb9tvrb6ocgh/.env 2>/dev/null | sort || echo "  (no .env found)"

REMOTE

echo ""
echo "────────────────────────────────────────────────"
echo "  Manual checks required:"
echo "  1. Cloudflare Tunnel: is overseer served via tunnel (no raw VPS IP)?"
echo "  2. Are secrets rotated in the last 90 days? (check ~/.secrets/master.env mtime)"
echo "  3. Are Docker images pulled/rebuilt in the last 30 days?"
echo "  4. Is there a vault backup separate from samhcharles/vault?"
echo "────────────────────────────────────────────────"
