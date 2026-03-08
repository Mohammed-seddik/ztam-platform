#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ZTAM — Tenant Onboarding Script
# Onboard a new client app (hosted anywhere) under ZTAM protection in ~30s.
#
# Usage:
#   ./scripts/onboard-tenant.sh \
#     --name      myapp                           \   # Keycloak client ID + OPA key
#     --backend   https://myapp.railway.app       \   # backend URL (any host)
#     --hostname  myapp.yourdomain.com            \   # hostname clients will hit
#     --roles     "admin,manager,user,viewer"         # comma-separated role list
#
# What it does:
#   1. Creates Keycloak client + roles via Admin REST API
#   2. Saves tenant config to tenants/<name>/config.json
#   3. Regenerates policies/tenants.json from tenant configs
#   4. Regenerates Envoy tenant routing from tenant configs
#   5. Reloads Envoy
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
TENANT_NAME=""
BACKEND_URL=""
HOSTNAME_FQDN=""
ROLES="admin,user,viewer"
LOGIN_MODE="form"
NO_SPI="false"

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)       TENANT_NAME="$2"; shift 2 ;;
        --backend)    BACKEND_URL="$2"; shift 2 ;;
        --hostname)   HOSTNAME_FQDN="$2"; shift 2 ;;
        --roles)      ROLES="$2"; shift 2 ;;
        --login-mode) LOGIN_MODE="$2"; shift 2 ;;
        --no-spi)     NO_SPI="true"; shift ;;
        -h|--help)
            grep '^#' "$0" | head -25 | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown arg: $1 (use --help for usage)"; exit 1 ;;
    esac
done

# Validate login-mode
if [[ "$LOGIN_MODE" != "form" && "$LOGIN_MODE" != "keycloak" ]]; then
    echo "ERROR: --login-mode must be 'form' or 'keycloak'"
    exit 1
fi

# ── Validate required args ────────────────────────────────────────────────────
[[ -z "$TENANT_NAME" ]]   && { echo "ERROR: --name is required";     exit 1; }
[[ -z "$BACKEND_URL" ]]   && { echo "ERROR: --backend is required";  exit 1; }
[[ -z "$HOSTNAME_FQDN" ]] && { echo "ERROR: --hostname is required"; exit 1; }

# Validate name is a safe identifier
if ! [[ "$TENANT_NAME" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
    echo "ERROR: --name must start with a letter and contain only a-z, A-Z, 0-9, _, -"
    exit 1
fi

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  ZTAM Tenant Onboarding: $TENANT_NAME"
echo "══════════════════════════════════════════════════════════════════════"
echo "  Backend:    $BACKEND_URL"
echo "  Hostname:   $HOSTNAME_FQDN"
echo "  Roles:      $ROLES"
echo "  Login Mode: $LOGIN_MODE"
echo "  No-SPI:     $NO_SPI"
echo ""

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ ! -f "$ROOT_DIR/.env" ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in values."
    exit 1
fi
set -o allexport
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +o allexport

KC_URL="${KEYCLOAK_URL:-http://localhost:8080}"
KC_REALM="${KC_REALM:-test-tenant}"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
# Support both KC_ADMIN_PASS (from .env.example) and KC_ADMIN_PASSWORD (legacy alias)
KC_ADMIN_PASSWORD="${KC_ADMIN_PASS:-${KC_ADMIN_PASSWORD:-}}"
[[ -z "$KC_ADMIN_PASSWORD" ]] && { echo "ERROR: KC_ADMIN_PASS not set in .env"; exit 1; }

# ── Step 1: Keycloak admin token ──────────────────────────────────────────────
echo "[1/5] Authenticating with Keycloak..."
KC_TOKEN=$(curl -sf \
    -d "client_id=admin-cli" \
    -d "username=${KC_ADMIN_USER}" \
    -d "password=${KC_ADMIN_PASSWORD}" \
    -d "grant_type=password" \
    "${KC_URL}/realms/master/protocol/openid-connect/token" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

[[ -z "$KC_TOKEN" ]] && { echo "ERROR: Failed to get Keycloak admin token. Is Keycloak running?"; exit 1; }
echo "   ✓ Keycloak admin authenticated"

# ── Step 2: Create Keycloak client ────────────────────────────────────────────
echo "[2/5] Registering Keycloak client '${TENANT_NAME}'..."

CLIENT_SECRET="ztam-$(openssl rand -hex 16)"
CLIENT_PUBLIC="false"
CLIENT_DIRECT_ACCESS="true"

if [[ "$LOGIN_MODE" == "keycloak" ]]; then
    CLIENT_PUBLIC="true"
    CLIENT_DIRECT_ACCESS="false"
fi

CLIENT_PAYLOAD=$(TENANT_NAME="$TENANT_NAME" \
HOSTNAME_FQDN="$HOSTNAME_FQDN" \
LOGIN_MODE="$LOGIN_MODE" \
CLIENT_SECRET="$CLIENT_SECRET" \
CLIENT_PUBLIC="$CLIENT_PUBLIC" \
CLIENT_DIRECT_ACCESS="$CLIENT_DIRECT_ACCESS" \
ZTAM_PUBLIC_URL="${ZTAM_PUBLIC_URL:-https://localhost}" \
python3 - <<'PY'
import json
import os

payload = {
    "clientId": os.environ["TENANT_NAME"],
    "name": os.environ["TENANT_NAME"],
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": os.environ["CLIENT_PUBLIC"] == "true",
    "directAccessGrantsEnabled": os.environ["CLIENT_DIRECT_ACCESS"] == "true",
    "standardFlowEnabled": True,
    "rootUrl": f"https://{os.environ['HOSTNAME_FQDN']}",
    "baseUrl": "/",
    "redirectUris": [
        f"https://{os.environ['HOSTNAME_FQDN']}/*",
        f"{os.environ['ZTAM_PUBLIC_URL']}/ztam/auth/callback*",
    ],
    "webOrigins": ["*"],
}

if os.environ["LOGIN_MODE"] != "keycloak":
    payload["secret"] = os.environ["CLIENT_SECRET"]

print(json.dumps(payload))
PY
)

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${KC_TOKEN}" \
    -H "Content-Type: application/json" \
    "${KC_URL}/admin/realms/${KC_REALM}/clients" \
    -d "$CLIENT_PAYLOAD")

if [[ "$HTTP_STATUS" == "409" ]]; then
    echo "   ⚠  Client '${TENANT_NAME}' already exists in Keycloak — skipping creation"
elif [[ "$HTTP_STATUS" == "201" ]]; then
    if [[ "$LOGIN_MODE" == "keycloak" ]]; then
        echo "   ✓ Client '${TENANT_NAME}' created (public client for hosted Keycloak login)"
    else
        echo "   ✓ Client '${TENANT_NAME}' created (secret: ${CLIENT_SECRET})"
        echo "   ⚠  Save this client secret — it will not be shown again"
    fi
    
    # If no-spi is NOT set, we associate the client with the common user-federation
    if [[ "$NO_SPI" == "false" ]]; then
        echo "   (SPI mode enabled: users will be served from external DB)"
        # Note: Linking to SPI usually happens at the realm level or via mappers.
        # onboard-tenant.sh currently assumes the realm is already configured with the SPI.
    fi
else
    echo "   ERROR: Keycloak client creation returned HTTP ${HTTP_STATUS}"
    exit 1
fi

# Get internal Keycloak client UUID
CLIENT_ID=$(curl -sf \
    -H "Authorization: Bearer ${KC_TOKEN}" \
    "${KC_URL}/admin/realms/${KC_REALM}/clients?clientId=${TENANT_NAME}" \
  | python3 -c "import sys,json; c=json.load(sys.stdin); print(c[0]['id']) if c else print('')")

[[ -z "$CLIENT_ID" ]] && { echo "ERROR: Could not resolve Keycloak client UUID for '${TENANT_NAME}'"; exit 1; }

# Create roles
IFS=',' read -ra ROLE_LIST <<< "$ROLES"
for ROLE in "${ROLE_LIST[@]}"; do
    ROLE=$(echo "$ROLE" | tr -d '[:space:]')
    [[ -z "$ROLE" ]] && continue
    R_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${KC_TOKEN}" \
        -H "Content-Type: application/json" \
        "${KC_URL}/admin/realms/${KC_REALM}/clients/${CLIENT_ID}/roles" \
        -d "{\"name\": \"${ROLE}\"}")
    if [[ "$R_STATUS" == "201" ]]; then
        echo "   ✓ Role '${ROLE}' created"
    else
        echo "   ⚠  Role '${ROLE}' — HTTP ${R_STATUS} (already exists or failed)"
    fi
done

# ── Step 3: OPA permissions ───────────────────────────────────────────────────
echo "[3/5] Adding OPA permissions for '${TENANT_NAME}'..."
echo "   ✓ Tenant policies are generated from tenants/<name>/config.json"

# ── Step 4: Tenant config ─────────────────────────────────────────────────────
echo "[4/5] Saving tenant config..."
python3 "$SCRIPT_DIR/tenant_manager.py" upsert \
        --tenants-dir "$ROOT_DIR/tenants" \
        --name "${TENANT_NAME}" \
        --backend-url "${BACKEND_URL}" \
        --hostname "${HOSTNAME_FQDN}" \
        --roles "${ROLES}" \
        --keycloak-client-id "${TENANT_NAME}" \
        --keycloak-realm "${KC_REALM}" \
        --login-mode "${LOGIN_MODE}" \
        $( [[ "$NO_SPI" == "true" ]] && printf '%s' '--no-spi' )
echo "   ✓ tenants/${TENANT_NAME}/config.json saved"

echo "   Generating policies/tenants.json from tenant configs..."
python3 "$SCRIPT_DIR/tenant_manager.py" sync-policies \
        --tenants-dir "$ROOT_DIR/tenants" \
        --output "$ROOT_DIR/policies/tenants.json"
echo "   ✓ policies/tenants.json updated (OPA reloads automatically)"

# ── Step 5: Envoy config ──────────────────────────────────────────────────────
echo "[5/5] Rendering Envoy config from tenant definitions..."
python3 "$SCRIPT_DIR/tenant_manager.py" sync-envoy \
    --tenants-dir "$ROOT_DIR/tenants" \
    --envoy-yaml "$ROOT_DIR/envoy/envoy.yaml"
echo "   ✓ envoy.yaml refreshed from tenant configs"

echo ""
echo "Reloading Envoy..."
cd "$ROOT_DIR"
docker compose restart envoy 2>/dev/null && echo "   ✓ Envoy reloaded" || echo "   ⚠  Envoy not running — start with: docker compose up -d"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  ✅  '${TENANT_NAME}' is now protected by ZTAM!"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "  Backend redirected:  ${BACKEND_URL}"
echo "  ZTAM entry point:    https://${HOSTNAME_FQDN}"
echo "  Keycloak client:     ${TENANT_NAME}  (realm: ${KC_REALM})"
echo ""
echo "  Headers forwarded to your backend (zero config needed):"
echo "    x-user-id:    <Keycloak user UUID>"
echo "    x-user-roles: <comma-separated roles>"
echo "    x-tenant-id:  ${TENANT_NAME}"
echo ""
echo "  Next steps:"
echo "  1. DNS: Point '${HOSTNAME_FQDN}' to this server's IP"
echo "  2. Users: Create users in Keycloak and assign the '${TENANT_NAME}' client roles"
echo "  3. Permissions: Fine-tune tenants/${TENANT_NAME}/config.json, then run scripts/tenant_manager.py sync-policies"
echo "  4. Optional: Read x-user-roles header in your backend for personalisation"
echo ""
