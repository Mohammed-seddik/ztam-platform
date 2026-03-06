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
#   2. Adds permissions to policies/tenants.json (OPA picks them up live)
#   3. Adds Envoy virtual host + cluster for the new backend
#   4. Saves tenant config to tenants/<name>/config.json
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

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)      TENANT_NAME="$2"; shift 2 ;;
        --backend)   BACKEND_URL="$2"; shift 2 ;;
        --hostname)  HOSTNAME_FQDN="$2"; shift 2 ;;
        --roles)     ROLES="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -20 | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown arg: $1 (use --help for usage)"; exit 1 ;;
    esac
done

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
echo "  Backend:  $BACKEND_URL"
echo "  Hostname: $HOSTNAME_FQDN"
echo "  Roles:    $ROLES"
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

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${KC_TOKEN}" \
    -H "Content-Type: application/json" \
    "${KC_URL}/admin/realms/${KC_REALM}/clients" \
    -d "{
      \"clientId\": \"${TENANT_NAME}\",
      \"name\": \"${TENANT_NAME}\",
      \"enabled\": true,
      \"protocol\": \"openid-connect\",
      \"publicClient\": false,
      \"directAccessGrantsEnabled\": true,
      \"standardFlowEnabled\": true,
      \"secret\": \"${CLIENT_SECRET}\"
    }")

if [[ "$HTTP_STATUS" == "409" ]]; then
    echo "   ⚠  Client '${TENANT_NAME}' already exists in Keycloak — skipping creation"
elif [[ "$HTTP_STATUS" == "201" ]]; then
    echo "   ✓ Client '${TENANT_NAME}' created (secret: ${CLIENT_SECRET})"
    echo "   ⚠  Save this client secret — it will not be shown again"
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
python3 "$SCRIPT_DIR/opa_add_tenant.py" \
    --name    "${TENANT_NAME}" \
    --roles   "${ROLES}" \
    --tenants "$ROOT_DIR/policies/tenants.json"
echo "   ✓ policies/tenants.json updated (OPA reloads automatically)"

# ── Step 4: Tenant config ─────────────────────────────────────────────────────
echo "[4/5] Saving tenant config..."
mkdir -p "$ROOT_DIR/tenants/${TENANT_NAME}"

BACKEND_HOST=$(python3 -c "from urllib.parse import urlparse; u=urlparse('${BACKEND_URL}'); print(u.hostname)")
BACKEND_PORT=$(python3 -c "from urllib.parse import urlparse; u=urlparse('${BACKEND_URL}'); p=u.port; print(p if p else (443 if u.scheme=='https' else 80))")
BACKEND_TLS=$(python3 -c "from urllib.parse import urlparse; u=urlparse('${BACKEND_URL}'); print('true' if u.scheme=='https' else 'false')")
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat > "$ROOT_DIR/tenants/${TENANT_NAME}/config.json" <<JSONEOF
{
  "name": "${TENANT_NAME}",
  "backend_url": "${BACKEND_URL}",
  "backend_host": "${BACKEND_HOST}",
  "backend_port": ${BACKEND_PORT},
  "backend_tls": ${BACKEND_TLS},
  "hostname": "${HOSTNAME_FQDN}",
  "keycloak_client_id": "${TENANT_NAME}",
  "keycloak_realm": "${KC_REALM}",
  "roles": "${ROLES}",
  "created_at": "${NOW}"
}
JSONEOF
echo "   ✓ tenants/${TENANT_NAME}/config.json saved"

# ── Step 5: Envoy config ──────────────────────────────────────────────────────
echo "[5/5] Updating Envoy config..."
python3 "$SCRIPT_DIR/envoy_add_tenant.py" \
    --name         "${TENANT_NAME}" \
    --hostname     "${HOSTNAME_FQDN}" \
    --backend-host "${BACKEND_HOST}" \
    --backend-port "${BACKEND_PORT}" \
    --backend-tls  "${BACKEND_TLS}" \
    --envoy-yaml   "$ROOT_DIR/envoy/envoy.yaml"

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
echo "  3. Permissions: Fine-tune  policies/tenants.json → '${TENANT_NAME}' entry"
echo "  4. Optional: Read x-user-roles header in your backend for personalisation"
echo ""
