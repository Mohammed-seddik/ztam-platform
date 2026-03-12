#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TENANT_NAME=""
BACKEND_URL=""
HOSTNAME_FQDN=""
ROLES="admin,editor,user,viewer"
LOGIN_MODE="keycloak"
ADAPTER_MODE="headers"
DOWNSTREAM_JWT_SECRET=""
NO_SPI="false"

DB_TYPE=""
DB_HOST=""
DB_PORT=""
DB_NAME=""
DB_USER=""
DB_PASSWORD=""
TABLE_NAME=""
USERNAME_COL=""
PASSWORD_COL=""
ROLE_COL=""
HASH_ALGORITHM=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --name) TENANT_NAME="$2"; shift 2 ;;
        --backend) BACKEND_URL="$2"; shift 2 ;;
        --hostname) HOSTNAME_FQDN="$2"; shift 2 ;;
        --roles) ROLES="$2"; shift 2 ;;
        --login-mode) LOGIN_MODE="$2"; shift 2 ;;
        --adapter-mode) ADAPTER_MODE="$2"; shift 2 ;;
        --downstream-jwt-secret) DOWNSTREAM_JWT_SECRET="$2"; shift 2 ;;
        --db-type) DB_TYPE="$2"; shift 2 ;;
        --db-host) DB_HOST="$2"; shift 2 ;;
        --db-port) DB_PORT="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --db-user) DB_USER="$2"; shift 2 ;;
        --db-password) DB_PASSWORD="$2"; shift 2 ;;
        --table-name) TABLE_NAME="$2"; shift 2 ;;
        --username-col) USERNAME_COL="$2"; shift 2 ;;
        --password-col) PASSWORD_COL="$2"; shift 2 ;;
        --role-col) ROLE_COL="$2"; shift 2 ;;
        --hash-algorithm) HASH_ALGORITHM="$2"; shift 2 ;;
        --no-spi) NO_SPI="true"; shift ;;
        -h|--help)
            cat <<'EOF'
Usage:
  ./scripts/onboard-tenant.sh \
    --name taskpro \
    --backend http://app:3001 \
    --hostname app.taskpro.com \
    --roles "admin,user" \
    --login-mode keycloak \
    --adapter-mode translated_token \
    --downstream-jwt-secret "<app-jwt-secret>" \
    --db-type mysql \
    --db-host db \
    --db-port 3306 \
    --db-name taskdb \
    --db-user root \
    --db-password rootpassword \
    --table-name Users \
    --username-col username \
    --password-col password \
    --role-col role \
    --hash-algorithm bcrypt

This creates an isolated Keycloak realm for the tenant, creates a tenant client,
registers a realm-local SPI user storage provider, creates the required protocol
mappers, writes tenants/<name>/config.json, regenerates published metadata, and
reloads Envoy.
EOF
            exit 0
            ;;
        *)
            echo "Unknown arg: $1"
            exit 1
            ;;
    esac
done

[[ -z "$TENANT_NAME" ]] && { echo "ERROR: --name is required"; exit 1; }
[[ -z "$BACKEND_URL" ]] && { echo "ERROR: --backend is required"; exit 1; }
[[ -z "$HOSTNAME_FQDN" ]] && { echo "ERROR: --hostname is required"; exit 1; }

if [[ "$LOGIN_MODE" != "form" && "$LOGIN_MODE" != "keycloak" ]]; then
    echo "ERROR: --login-mode must be 'form' or 'keycloak'"
    exit 1
fi
if [[ "$ADAPTER_MODE" != "headers" && "$ADAPTER_MODE" != "translated_token" ]]; then
    echo "ERROR: --adapter-mode must be 'headers' or 'translated_token'"
    exit 1
fi
if [[ "$ADAPTER_MODE" == "translated_token" && -z "$DOWNSTREAM_JWT_SECRET" ]]; then
    echo "ERROR: --downstream-jwt-secret is required when --adapter-mode translated_token"
    exit 1
fi
if ! [[ "$TENANT_NAME" =~ ^[a-z][a-z0-9_-]{1,63}$ ]]; then
    echo "ERROR: --name must match ^[a-z][a-z0-9_-]{1,63}$"
    exit 1
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
    echo "ERROR: .env not found"
    exit 1
fi

set -o allexport
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +o allexport

KC_URL="${KEYCLOAK_URL:-http://localhost:8080}"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASS:-${KC_ADMIN_PASSWORD:-}}"
[[ -z "$KC_ADMIN_PASSWORD" ]] && { echo "ERROR: KC_ADMIN_PASS not set"; exit 1; }

CONFIG_PATH="$ROOT_DIR/tenants/$TENANT_NAME/config.json"
if [[ -f "$CONFIG_PATH" ]]; then
    eval "$(
        CONFIG_PATH="$CONFIG_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
raw = json.loads(path.read_text(encoding="utf-8"))
db = raw.get("db_credentials") or {}
for key in (
    "db_type",
    "db_host",
    "db_port",
    "db_name",
    "db_user",
    "db_password",
    "table_name",
    "username_col",
    "password_col",
    "role_col",
    "hash_algorithm",
):
    value = str(db.get(key, "")).replace("\\", "\\\\").replace('"', '\\"')
    print(f'EXISTING_{key.upper()}="{value}"')
print(f'EXISTING_ADAPTER_MODE="{str(raw.get("adapter_mode", "")).replace(chr(34), chr(92) + chr(34))}"')
print(f'EXISTING_DOWNSTREAM_JWT_SECRET="{str(raw.get("downstream_jwt_secret", "")).replace(chr(34), chr(92) + chr(34))}"')
PY
    )"
    DB_TYPE="${DB_TYPE:-${EXISTING_DB_TYPE:-}}"
    DB_HOST="${DB_HOST:-${EXISTING_DB_HOST:-}}"
    DB_PORT="${DB_PORT:-${EXISTING_DB_PORT:-}}"
    DB_NAME="${DB_NAME:-${EXISTING_DB_NAME:-}}"
    DB_USER="${DB_USER:-${EXISTING_DB_USER:-}}"
    DB_PASSWORD="${DB_PASSWORD:-${EXISTING_DB_PASSWORD:-}}"
    TABLE_NAME="${TABLE_NAME:-${EXISTING_TABLE_NAME:-}}"
    USERNAME_COL="${USERNAME_COL:-${EXISTING_USERNAME_COL:-}}"
    PASSWORD_COL="${PASSWORD_COL:-${EXISTING_PASSWORD_COL:-}}"
    ROLE_COL="${ROLE_COL:-${EXISTING_ROLE_COL:-}}"
    HASH_ALGORITHM="${HASH_ALGORITHM:-${EXISTING_HASH_ALGORITHM:-}}"
    if [[ -z "$DOWNSTREAM_JWT_SECRET" ]]; then
        DOWNSTREAM_JWT_SECRET="${EXISTING_DOWNSTREAM_JWT_SECRET:-}"
    fi
    if [[ "$ADAPTER_MODE" == "headers" && -n "${EXISTING_ADAPTER_MODE:-}" ]]; then
        ADAPTER_MODE="$EXISTING_ADAPTER_MODE"
    fi
fi

if [[ "$NO_SPI" == "false" ]]; then
    [[ -z "$DB_TYPE" ]] && DB_TYPE="mysql"
    [[ -z "$DB_PORT" ]] && DB_PORT=$([[ "$DB_TYPE" == "postgresql" ]] && echo "5432" || echo "3306")
    [[ -z "$TABLE_NAME" ]] && TABLE_NAME="users"
    [[ -z "$USERNAME_COL" ]] && USERNAME_COL="username"
    [[ -z "$PASSWORD_COL" ]] && PASSWORD_COL="password_hash"
    [[ -z "$ROLE_COL" ]] && ROLE_COL="role"
    [[ -z "$HASH_ALGORITHM" ]] && HASH_ALGORITHM="bcrypt"

    for required in DB_TYPE DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD TABLE_NAME USERNAME_COL PASSWORD_COL ROLE_COL HASH_ALGORITHM; do
        if [[ -z "${!required}" ]]; then
            echo "ERROR: missing required SPI setting: ${required}"
            exit 1
        fi
    done
fi

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  ZTAM Tenant Onboarding: $TENANT_NAME"
echo "══════════════════════════════════════════════════════════════════════"
echo "  Backend:      $BACKEND_URL"
echo "  Hostname:     $HOSTNAME_FQDN"
echo "  Realm:        $TENANT_NAME"
echo "  Roles:        $ROLES"
echo "  Login Mode:   $LOGIN_MODE"
echo "  Adapter Mode: $ADAPTER_MODE"
echo "  SPI Enabled:  $([[ "$NO_SPI" == "true" ]] && echo "no" || echo "yes")"
echo ""

kc_json() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    if [[ -n "$data" ]]; then
        curl -sf -X "$method" \
            -H "Authorization: Bearer ${KC_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KC_URL}${path}" \
            -d "$data"
    else
        curl -sf -X "$method" \
            -H "Authorization: Bearer ${KC_TOKEN}" \
            "${KC_URL}${path}"
    fi
}

kc_status() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    if [[ -n "$data" ]]; then
        curl -s -o /dev/null -w "%{http_code}" -X "$method" \
            -H "Authorization: Bearer ${KC_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KC_URL}${path}" \
            -d "$data"
    else
        curl -s -o /dev/null -w "%{http_code}" -X "$method" \
            -H "Authorization: Bearer ${KC_TOKEN}" \
            "${KC_URL}${path}"
    fi
}

echo "[1/7] Authenticating with Keycloak..."
KC_TOKEN="$(curl -sf \
    -d "client_id=admin-cli" \
    -d "username=${KC_ADMIN_USER}" \
    -d "password=${KC_ADMIN_PASSWORD}" \
    -d "grant_type=password" \
    "${KC_URL}/realms/master/protocol/openid-connect/token" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")"
[[ -z "$KC_TOKEN" ]] && { echo "ERROR: failed to obtain Keycloak admin token"; exit 1; }
echo "   ✓ Keycloak admin authenticated"

echo "[2/7] Ensuring realm '${TENANT_NAME}' exists..."
REALM_PAYLOAD="$(TENANT_NAME="$TENANT_NAME" python3 - <<'PY'
import json
import os
payload = {
    "realm": os.environ["TENANT_NAME"],
    "enabled": True,
    "displayName": os.environ["TENANT_NAME"],
    "registrationAllowed": False,
    "loginTheme": "keycloak",
    "accessTokenLifespan": 900,
    "ssoSessionIdleTimeout": 1800,
    "ssoSessionMaxLifespan": 36000,
}
print(json.dumps(payload))
PY
)"
REALM_STATUS="$(kc_status POST /admin/realms "$REALM_PAYLOAD")"
if [[ "$REALM_STATUS" == "201" ]]; then
    echo "   ✓ Realm created"
elif [[ "$REALM_STATUS" == "409" ]]; then
    echo "   ✓ Realm already exists"
else
    echo "ERROR: realm creation returned HTTP ${REALM_STATUS}"
    exit 1
fi

REALM_DOC="$(kc_json GET "/admin/realms/${TENANT_NAME}")"
REALM_ID="$(REALM_DOC="$REALM_DOC" python3 - <<'PY'
import json
import os
print(json.loads(os.environ["REALM_DOC"]).get("id", ""))
PY
)"
[[ -z "$REALM_ID" ]] && { echo "ERROR: could not resolve realm id for ${TENANT_NAME}"; exit 1; }
REALM_UPDATE_PAYLOAD="$(REALM_DOC="$REALM_DOC" python3 - <<'PY'
import json
import os
doc = json.loads(os.environ["REALM_DOC"])
doc.update({
    "enabled": True,
    "registrationAllowed": False,
    "loginTheme": "keycloak",
    "accessTokenLifespan": 900,
    "ssoSessionIdleTimeout": 1800,
    "ssoSessionMaxLifespan": 36000,
})
print(json.dumps(doc))
PY
)"
UPDATE_STATUS="$(kc_status PUT "/admin/realms/${TENANT_NAME}" "$REALM_UPDATE_PAYLOAD")"
if [[ "$UPDATE_STATUS" != "204" && "$UPDATE_STATUS" != "200" ]]; then
    echo "ERROR: realm update returned HTTP ${UPDATE_STATUS}"
    exit 1
fi
echo "   ✓ Realm settings synchronized"

PROFILE_DOC="$(kc_json GET "/admin/realms/${TENANT_NAME}/users/profile")"
PROFILE_PAYLOAD="$(PROFILE_DOC="$PROFILE_DOC" python3 - <<'PY'
import json
import os

profile = json.loads(os.environ["PROFILE_DOC"])
for attribute in profile.get("attributes", []):
    name = attribute.get("name")
    if name in {"email", "firstName", "lastName"}:
        attribute.pop("required", None)
    if name == "email":
        validations = dict(attribute.get("validations") or {})
        validations.pop("email", None)
        if validations:
            attribute["validations"] = validations
        else:
            attribute.pop("validations", None)
print(json.dumps(profile))
PY
)"
PROFILE_STATUS="$(kc_status PUT "/admin/realms/${TENANT_NAME}/users/profile" "$PROFILE_PAYLOAD")"
if [[ "$PROFILE_STATUS" != "204" && "$PROFILE_STATUS" != "200" ]]; then
    echo "ERROR: realm user-profile update returned HTTP ${PROFILE_STATUS}"
    exit 1
fi
echo "   ✓ Realm user-profile relaxed for federated usernames"

echo "[3/7] Ensuring tenant client '${TENANT_NAME}' exists in realm '${TENANT_NAME}'..."
CLIENT_LOOKUP="$(kc_json GET "/admin/realms/${TENANT_NAME}/clients?clientId=${TENANT_NAME}")"
CLIENT_UUID="$(CLIENT_LOOKUP="$CLIENT_LOOKUP" python3 - <<'PY'
import json
import os
items = json.loads(os.environ["CLIENT_LOOKUP"])
print(items[0]["id"] if items else "")
PY
)"
CLIENT_SECRET="ztam-${TENANT_NAME}-$(openssl rand -hex 12)"
CLIENT_PAYLOAD="$(TENANT_NAME="$TENANT_NAME" HOSTNAME_FQDN="$HOSTNAME_FQDN" CLIENT_SECRET="$CLIENT_SECRET" python3 - <<'PY'
import json
import os
tenant = os.environ["TENANT_NAME"]
hostname = os.environ["HOSTNAME_FQDN"]
payload = {
    "clientId": tenant,
    "name": tenant,
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": False,
    "directAccessGrantsEnabled": True,
    "standardFlowEnabled": True,
    "serviceAccountsEnabled": False,
    "secret": os.environ["CLIENT_SECRET"],
    "rootUrl": f"https://{hostname}",
    "baseUrl": "/",
    "redirectUris": [
        f"https://{hostname}/*",
        f"https://{hostname}/ztam/auth/callback*",
        f"https://{hostname}/api/auth/callback*",
        f"https://{hostname}/auth/callback*",
    ],
    "webOrigins": [f"https://{hostname}"],
}
print(json.dumps(payload))
PY
)"

if [[ -z "$CLIENT_UUID" ]]; then
    CLIENT_STATUS="$(kc_status POST "/admin/realms/${TENANT_NAME}/clients" "$CLIENT_PAYLOAD")"
    if [[ "$CLIENT_STATUS" != "201" ]]; then
        echo "ERROR: client creation returned HTTP ${CLIENT_STATUS}"
        exit 1
    fi
    CLIENT_LOOKUP="$(kc_json GET "/admin/realms/${TENANT_NAME}/clients?clientId=${TENANT_NAME}")"
    CLIENT_UUID="$(CLIENT_LOOKUP="$CLIENT_LOOKUP" python3 - <<'PY'
import json
import os
items = json.loads(os.environ["CLIENT_LOOKUP"])
print(items[0]["id"] if items else "")
PY
)"
    echo "   ✓ Client created"
else
    UPDATE_CLIENT_STATUS="$(kc_status PUT "/admin/realms/${TENANT_NAME}/clients/${CLIENT_UUID}" "$CLIENT_PAYLOAD")"
    if [[ "$UPDATE_CLIENT_STATUS" != "204" && "$UPDATE_CLIENT_STATUS" != "200" ]]; then
        echo "ERROR: client update returned HTTP ${UPDATE_CLIENT_STATUS}"
        exit 1
    fi
    echo "   ✓ Client updated"
fi

[[ -z "$CLIENT_UUID" ]] && { echo "ERROR: could not resolve client UUID"; exit 1; }

echo "   ✓ Client secret synchronized"

echo "[4/7] Ensuring realm-local protocol mappers exist..."
MAPPERS_JSON="$(kc_json GET "/admin/realms/${TENANT_NAME}/clients/${CLIENT_UUID}/protocol-mappers/models")"
for mapper_name in ztam-role ztam-db-user-id; do
    if MAPPERS_JSON="$MAPPERS_JSON" MAPPER_NAME="$mapper_name" python3 - <<'PY'
import json
import os
name = os.environ["MAPPER_NAME"]
items = json.loads(os.environ["MAPPERS_JSON"])
raise SystemExit(0 if any(item.get("name") == name for item in items) else 1)
PY
    then
        echo "   ✓ Mapper '${mapper_name}' already present"
        continue
    fi

    if [[ "$mapper_name" == "ztam-role" ]]; then
        MAPPER_PAYLOAD='{"name":"ztam-role","protocol":"openid-connect","protocolMapper":"oidc-usermodel-attribute-mapper","consentRequired":false,"config":{"user.attribute":"role","claim.name":"role","jsonType.label":"String","id.token.claim":"true","access.token.claim":"true","userinfo.token.claim":"true","aggregate.attrs":"false","multivalued":"false"}}'
    else
        MAPPER_PAYLOAD='{"name":"ztam-db-user-id","protocol":"openid-connect","protocolMapper":"oidc-usermodel-attribute-mapper","consentRequired":false,"config":{"user.attribute":"db_user_id","claim.name":"db_user_id","jsonType.label":"String","id.token.claim":"true","access.token.claim":"true","userinfo.token.claim":"true","aggregate.attrs":"false","multivalued":"false"}}'
    fi
    MAPPER_STATUS="$(kc_status POST "/admin/realms/${TENANT_NAME}/clients/${CLIENT_UUID}/protocol-mappers/models" "$MAPPER_PAYLOAD")"
    if [[ "$MAPPER_STATUS" != "201" ]]; then
        echo "ERROR: protocol mapper '${mapper_name}' creation returned HTTP ${MAPPER_STATUS}"
        exit 1
    fi
    echo "   ✓ Mapper '${mapper_name}' created"
done

echo "[5/7] Ensuring realm-local SPI user storage provider exists..."
if [[ "$NO_SPI" == "true" ]]; then
    echo "   ✓ Skipped (--no-spi)"
else
    COMPONENT_LOOKUP="$(kc_json GET "/admin/realms/${TENANT_NAME}/components?type=org.keycloak.storage.UserStorageProvider")"
    COMPONENT_ID="$(COMPONENT_LOOKUP="$COMPONENT_LOOKUP" TENANT_NAME="$TENANT_NAME" python3 - <<'PY'
import json
import os
items = json.loads(os.environ["COMPONENT_LOOKUP"])
name = f"{os.environ['TENANT_NAME']}-db"
for item in items:
    if item.get("providerId") == "mysql-db-provider" and item.get("name") == name:
        print(item.get("id", ""))
        break
else:
    print("")
PY
)"
    COMPONENT_PAYLOAD="$(TENANT_NAME="$TENANT_NAME" REALM_ID="$REALM_ID" DB_TYPE="$DB_TYPE" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_NAME="$DB_NAME" DB_USER="$DB_USER" DB_PASSWORD="$DB_PASSWORD" TABLE_NAME="$TABLE_NAME" USERNAME_COL="$USERNAME_COL" PASSWORD_COL="$PASSWORD_COL" ROLE_COL="$ROLE_COL" HASH_ALGORITHM="$HASH_ALGORITHM" python3 - <<'PY'
import json
import os
payload = {
    "name": f"{os.environ['TENANT_NAME']}-db",
    "providerId": "mysql-db-provider",
    "providerType": "org.keycloak.storage.UserStorageProvider",
    "parentId": os.environ["REALM_ID"],
    "config": {
        "db_type": [os.environ["DB_TYPE"]],
        "db_host": [os.environ["DB_HOST"]],
        "db_port": [os.environ["DB_PORT"]],
        "db_name": [os.environ["DB_NAME"]],
        "db_user": [os.environ["DB_USER"]],
        "db_pass": [os.environ["DB_PASSWORD"]],
        "table_name": [os.environ["TABLE_NAME"]],
        "username_col": [os.environ["USERNAME_COL"]],
        "password_col": [os.environ["PASSWORD_COL"]],
        "role_col": [os.environ["ROLE_COL"]],
        "hash_algorithm": [os.environ["HASH_ALGORITHM"]],
        "enabled": ["true"],
        "priority": ["0"],
        "cachePolicy": ["DEFAULT"],
    },
}
print(json.dumps(payload))
PY
)"
    if [[ -n "$COMPONENT_ID" ]]; then
        COMPONENT_STATUS="$(kc_status PUT "/admin/realms/${TENANT_NAME}/components/${COMPONENT_ID}" "$COMPONENT_PAYLOAD")"
        if [[ "$COMPONENT_STATUS" != "204" && "$COMPONENT_STATUS" != "200" ]]; then
            echo "ERROR: SPI component update returned HTTP ${COMPONENT_STATUS}"
            exit 1
        fi
        echo "   ✓ SPI component updated"
    else
        COMPONENT_STATUS="$(kc_status POST "/admin/realms/${TENANT_NAME}/components" "$COMPONENT_PAYLOAD")"
        if [[ "$COMPONENT_STATUS" != "201" ]]; then
            echo "ERROR: SPI component creation returned HTTP ${COMPONENT_STATUS}"
            exit 1
        fi
        echo "   ✓ SPI component created"
    fi
fi

echo "[6/7] Writing tenant config and published metadata..."
TENANT_ARGS=(
    python3 "$SCRIPT_DIR/tenant_manager.py" upsert
    --tenants-dir "$ROOT_DIR/tenants"
    --name "$TENANT_NAME"
    --backend-url "$BACKEND_URL"
    --hostname "$HOSTNAME_FQDN"
    --roles "$ROLES"
    --keycloak-client-id "$TENANT_NAME"
    --keycloak-realm "$TENANT_NAME"
    --keycloak-client-secret "$CLIENT_SECRET"
    --login-mode "$LOGIN_MODE"
    --adapter-mode "$ADAPTER_MODE"
)
if [[ -n "$DOWNSTREAM_JWT_SECRET" ]]; then
    TENANT_ARGS+=(--downstream-jwt-secret "$DOWNSTREAM_JWT_SECRET")
fi
if [[ "$NO_SPI" == "true" ]]; then
    TENANT_ARGS+=(--no-spi)
else
    TENANT_ARGS+=(
        --db-type "$DB_TYPE"
        --db-host "$DB_HOST"
        --db-port "$DB_PORT"
        --db-name "$DB_NAME"
        --db-user "$DB_USER"
        --db-password "$DB_PASSWORD"
        --table-name "$TABLE_NAME"
        --username-col "$USERNAME_COL"
        --password-col "$PASSWORD_COL"
        --role-col "$ROLE_COL"
        --hash-algorithm "$HASH_ALGORITHM"
    )
fi
"${TENANT_ARGS[@]}"

python3 "$SCRIPT_DIR/tenant_manager.py" sync-policies \
    --tenants-dir "$ROOT_DIR/tenants" \
    --output "$ROOT_DIR/policies/tenants.json"
python3 "$SCRIPT_DIR/tenant_manager.py" sync-auth \
    --tenants-dir "$ROOT_DIR/tenants" \
    --output "$ROOT_DIR/platform/published/auth/tenants.json"

echo "[7/7] Rendering Envoy config and reloading gateway..."
python3 "$SCRIPT_DIR/tenant_manager.py" sync-envoy \
    --tenants-dir "$ROOT_DIR/tenants" \
    --envoy-yaml "$ROOT_DIR/envoy/envoy.yaml"
cd "$ROOT_DIR"
docker compose restart envoy >/dev/null 2>&1 && echo "   ✓ Envoy reloaded" || echo "   ⚠ Envoy restart skipped"

echo ""
echo "Tenant onboarded:"
echo "  name:                 $TENANT_NAME"
echo "  realm:                $TENANT_NAME"
echo "  client_id:            $TENANT_NAME"
echo "  backend:              $BACKEND_URL"
echo "  hostname:             $HOSTNAME_FQDN"
echo "  adapter_mode:         $ADAPTER_MODE"
echo "  client_secret_saved:  tenants/$TENANT_NAME/config.json"
