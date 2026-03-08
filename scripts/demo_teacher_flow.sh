#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND_URL="${1:-https://store-app-wmzx.onrender.com}"
CLIENT_NAME="${2:-teacherdemo}"
CLIENT_HOSTNAME="${3:-teacherdemo.ztam.local}"
CLIENT_ROLES="${4:-admin,manager,user}"

cd "$ROOT_DIR"

echo "============================================================"
echo "ZTAM TEACHER DEMO FLOW"
echo "============================================================"
echo
echo "[1/4] Validate tenant model"
python3 scripts/tenant_manager.py validate

echo
echo "[2/4] Prove working protection on bundled demo tenant"
python3 scripts/smoke_test_tenant.py \
  --base-url https://localhost \
  --protected-path /api/tasks \
  --login-mode form \
  --username alice \
  --password secret123 \
  --expect-status 200 \
  --insecure

python3 scripts/smoke_test_tenant.py \
  --base-url https://localhost \
  --protected-path /admin \
  --login-mode form \
  --username charlie \
  --password pass123 \
  --expect-status 403 \
  --insecure

echo
echo "[3/4] Assess a client website dynamically"
temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT

python3 scripts/tenant_manager.py assess \
  --backend-url "$BACKEND_URL" \
  --name "$CLIENT_NAME" \
  --hostname "$CLIENT_HOSTNAME" \
  --roles "$CLIENT_ROLES" \
  --write-config \
  --tenants-dir "$temp_dir"

echo
echo "Generated starter tenant config:"
cat "$temp_dir/$CLIENT_NAME/config.json"

echo
echo "[4/4] Suggested next command for a real onboarding"
echo "./scripts/onboard-tenant.sh --name $CLIENT_NAME --backend $BACKEND_URL --hostname $CLIENT_HOSTNAME --roles \"$CLIENT_ROLES\""

echo
echo "Demo complete."