#!/usr/bin/env bash
# Overseer VPS security audit — runs remotely over SSH, prints a scored report.
# Usage: ./audit-security.sh [vps-host]
set -euo pipefail

VPS=${1:-root@100.73.12.59}
PASS=0; FAIL=0; WARN=0

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
CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 5 -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" -d '{"message":"ping"}' 2>/dev/null || echo "000")
echo "  /chat no-auth → HTTP $CODE  (want 401)"

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" -m 5 http://localhost:8765/health 2>/dev/null || echo "000")
echo "  /health no-auth → HTTP $HEALTH  (want 200)"

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
echo "  4. Is there a vault backup separate from samhcharles/brain?"
echo "────────────────────────────────────────────────"
