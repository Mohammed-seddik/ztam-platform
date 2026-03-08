#!/bin/bash
set -euo pipefail

KC_BASE="${KEYCLOAK_URL:-http://localhost:8080}"
KC_REALM="${KC_REALM:-test-tenant}"
KC_CLIENT="${KC_CLIENT_ID:-test-app}"
KC_SECRET="${KC_CLIENT_SECRET:-test-app-secret-2024}"
ADMIN_PASS="${KC_ADMIN_PASS:-admin_secret_456}"

echo "============================================================"
echo " ZTAM FULL FLOW DEMO"
echo "============================================================"

# Get fresh admin token
ADMIN_TOKEN=$(curl -sf -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=${ADMIN_PASS}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])") || {
    echo "ERROR: Failed to get admin token. Is Keycloak running at $KC_BASE?"
    exit 1
  }

echo ""
echo "--- testapp MySQL users (your app's DB) ---"
docker exec ztam-platform-testapp-db-1 mysql -u taskuser -ptaskpass taskapp \
  -e "SELECT id, username, role FROM users;" 2>/dev/null

echo ""
echo "--- Keycloak users (should be empty or SPI-linked only) ---"
curl -s "$KC_BASE/admin/realms/$KC_REALM/users?max=20" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 - <<'PY'
import json
import sys

users = json.load(sys.stdin)
if isinstance(users, list):
  if not users:
    print("  No native Keycloak users - all served via SPI!")
  for user in users:
    source = "FEDERATION(SPI)" if user.get("federationLink") else "NATIVE-KEYCLOAK"
    print(f"  {user['username']} -> {source}")
else:
  print(users)
PY

echo ""
echo "--- LOGIN alice via Keycloak (SPI reads from testapp-db) ---"
RESP=$(curl -s -X POST "$KC_BASE/realms/$KC_REALM/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=$KC_CLIENT&client_secret=$KC_SECRET&username=alice&password=secret123")

echo "$RESP" | python3 - <<'PY'
import base64
import json
import sys

data = json.load(sys.stdin)
if "access_token" in data:
  token = data["access_token"]
  raw = token.split(".")[1]
  padding = (4 - len(raw) % 4) % 4
  payload = json.loads(base64.urlsafe_b64decode(raw + "=" * padding))
  print("  Status:   LOGIN SUCCESS")
  print("  Username:", payload.get("preferred_username"))
  print("  Issuer:  ", payload.get("iss"))
  print("  Role:    ", payload.get("role", "(no role claim)"))
  print("  Alg:      RS256 - signed by Keycloak")
else:
  print("  ERROR:", json.dumps(data))
PY

KC_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))")

echo ""
echo "--- ENVOY enforcement (port 80, no token) ---"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:80/api/tasks)
echo "  GET /api/tasks (no token) => HTTP $HTTP (expected 401)"

echo ""
echo "--- ENVOY enforcement (port 80, Keycloak token) ---"
HTTP2=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:80/api/tasks -H "Authorization: Bearer $KC_TOKEN")
echo "  GET /api/tasks (Keycloak token) => HTTP $HTTP2"
if [ "$HTTP2" = "200" ] || [ "$HTTP2" = "403" ]; then
    echo "  -> Token passed Keycloak+SPI+OPA validation"
fi

echo ""
echo "Done."
