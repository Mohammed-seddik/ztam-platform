#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ZTAM — Tenant Offboarding Script
# Remove a protected client app and all its configurations.
#
# Usage:
#   ./scripts/offboard-tenant.sh --name myapp
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TENANT_NAME=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)      TENANT_NAME="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -10 | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown arg: $1 (use --help for usage)"; exit 1 ;;
    esac
done

[[ -z "$TENANT_NAME" ]] && { echo "ERROR: --name is required"; exit 1; }

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  ZTAM Tenant Offboarding: $TENANT_NAME"
echo "══════════════════════════════════════════════════════════════════════"
echo ""

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ ! -f "$ROOT_DIR/.env" ]]; then
    source "$ROOT_DIR/.env.example"
else
    set -o allexport
    source "$ROOT_DIR/.env"
    set +o allexport
fi

KC_URL="${KEYCLOAK_URL:-http://localhost:8080}"
KC_REALM="${KC_REALM:-test-tenant}"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASS:-${KC_ADMIN_PASSWORD:-}}"

# ── Step 1: Keycloak admin token ──────────────────────────────────────────────
echo "[1/4] Authenticating with Keycloak..."
KC_TOKEN=$(curl -sf \
    -d "client_id=admin-cli" \
    -d "username=${KC_ADMIN_USER}" \
    -d "password=${KC_ADMIN_PASSWORD}" \
    -d "grant_type=password" \
    "${KC_URL}/realms/master/protocol/openid-connect/token" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

[[ -z "$KC_TOKEN" ]] && { echo "ERROR: Failed to get Keycloak admin token"; exit 1; }

# ── Step 2: Delete Keycloak client ────────────────────────────────────────────
echo "[2/4] Deleting Keycloak client '${TENANT_NAME}'..."
CLIENT_ID=$(curl -sf \
    -H "Authorization: Bearer ${KC_TOKEN}" \
    "${KC_URL}/admin/realms/${KC_REALM}/clients?clientId=${TENANT_NAME}" \
  | python3 -c "import sys,json; c=json.load(sys.stdin); print(c[0]['id']) if c else print('')")

if [[ -n "$CLIENT_ID" ]]; then
    curl -sf -X DELETE \
        -H "Authorization: Bearer ${KC_TOKEN}" \
        "${KC_URL}/admin/realms/${KC_REALM}/clients/${CLIENT_ID}"
    echo "   ✓ Keycloak client deleted"
else
    echo "   ⚠  Keycloak client not found — skipping"
fi

# ── Step 3: Remove OPA permissions ────────────────────────────────────────────
echo "[3/4] Removing OPA permissions..."
if [[ -f "$ROOT_DIR/policies/tenants.json" ]]; then
    python3 -c "import json, sys; d=json.load(open('$ROOT_DIR/policies/tenants.json')); d.pop('${TENANT_NAME}', None); json.dump(d, open('$ROOT_DIR/policies/tenants.json', 'w'), indent=2)"
    echo "   ✓ policies/tenants.json updated"
fi

# ── Step 4: Remove Envoy config & Files ───────────────────────────────────────
echo "[4/4] Cleaning up Envoy config and local files..."

# Remove VHost from envoy.yaml
# We look for the block starting with '# ── Tenant: <name>' and ending with an empty line
# This is a bit risky with sed, but let's try a python one-liner for safety
python3 -c "
import re
path = '$ROOT_DIR/envoy/envoy.yaml'
with open(path, 'r') as f:
    c = f.read()
# Regex to match the tenant block in vhosts and clusters
c = re.sub(r' +-# ── Tenant: ${TENANT_NAME}.*?\n\n', '', c, flags=re.DOTALL)
c = re.sub(r'    - name: ${TENANT_NAME}_cluster.*?\n\n', '', c, flags=re.DOTALL)
with open(path, 'w') as f:
    f.write(c)
"
echo "   ✓ envoy.yaml cleaned"

# Delete tenant directory
if [[ -d "$ROOT_DIR/tenants/${TENANT_NAME}" ]]; then
    rm -rf "$ROOT_DIR/tenants/${TENANT_NAME}"
    echo "   ✓ tenants/${TENANT_NAME}/ directory deleted"
fi

echo ""
echo "Reloading Envoy..."
cd "$ROOT_DIR"
docker compose restart envoy 2>/dev/null && echo "   ✓ Envoy reloaded"

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  ✅  '${TENANT_NAME}' offboarded successfully."
echo "══════════════════════════════════════════════════════════════════════"
echo ""
