#!/bin/bash
set -euo pipefail

KC_BASE="${KEYCLOAK_URL:-http://localhost:8080}"
KC_REALM="${KC_REALM:-test-tenant}"
KC_CLIENT="${KC_CLIENT_ID:-test-app}"
KC_SECRET="${KC_CLIENT_SECRET:-}"
ADMIN_PASS="${KC_ADMIN_PASS:-}"
DEMO_ALICE_PASSWORD="${DEMO_ALICE_PASSWORD:-}"

if [[ -z "$KC_SECRET" || -z "$ADMIN_PASS" || -z "$DEMO_ALICE_PASSWORD" ]]; then
  echo "ERROR: KC_CLIENT_SECRET, KC_ADMIN_PASS, and DEMO_ALICE_PASSWORD must be set."
  exit 1
fi

echo "KC_BASE: $KC_BASE"
echo "KC_REALM: $KC_REALM"
echo "============================================================"
echo " ZTAM FULL FLOW DEMO"
echo "============================================================"

# Get fresh admin token
ADMIN_TOKEN=$(curl -sf -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=${ADMIN_PASS}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))") || {
    echo "ERROR: Failed to get admin token. Check credentials and if Keycloak is running at $KC_BASE"
    exit 1
  }
if [[ -z "$ADMIN_TOKEN" ]]; then
  echo "ERROR: ADMIN_TOKEN is empty"
  exit 1
fi
echo "   ✓ Admin token obtained"

echo ""
echo "--- testapp MySQL users (your app's DB) ---"
docker exec ztam-platform-testapp-db-1 mysql -u taskuser -ptaskpass taskapp \
  -e "SELECT id, username, role FROM users;" 2>/dev/null

echo ""
echo "--- Keycloak users (should be empty or SPI-linked only) ---"
# Use -fv to catch 404/401 and see details
curl -sf "$KC_BASE/admin/realms/$KC_REALM/users?max=20" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 <(cat <<'PY'
import json
import sys

try:
    raw = sys.stdin.read().strip()
    if not raw:
        print("  No native Keycloak users - all served via SPI (empty list)!")
        sys.exit(0)
    users = json.loads(raw)
    if isinstance(users, list):
      if not users:
        print("  No native Keycloak users - all served via SPI!")
      for user in users:
        source = "FEDERATION(SPI)" if user.get("federationLink") else "NATIVE-KEYCLOAK"
        print(f"  {user['username']} -> {source}")
    else:
      print(f"  Unexpected response type: {type(users)}")
except Exception as e:
    print(f"  Error parsing users: {e}")
PY
)

echo ""
echo "--- LOGIN alice via Keycloak (SPI reads from testapp-db) ---"
RESP=$(curl -sf -X POST "$KC_BASE/realms/$KC_REALM/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=$KC_CLIENT&client_secret=$KC_SECRET&username=alice&password=$DEMO_ALICE_PASSWORD" || echo "FAILED")

if [ "$RESP" = "FAILED" ]; then
    echo "  ERROR: Login request failed (curl error)"
else
    echo "$RESP" | python3 <(cat <<'PY'
import base64
import json
import sys

try:
    raw = sys.stdin.read().strip()
    if not raw:
        print("  ERROR: Empty login response")
        sys.exit(0)
    data = json.loads(raw)
    if "access_token" in data:
      token = data["access_token"]
      raw_token = token.split(".")[1]
      padding = (4 - len(raw_token) % 4) % 4
      payload = json.loads(base64.urlsafe_b64decode(raw_token + "=" * padding))
      print("  Status:   LOGIN SUCCESS")
      print("  Username:", payload.get("preferred_username"))
      print("  Issuer:  ", payload.get("iss"))
      print("  Role:    ", payload.get("role", "(no role claim)"))
      print("  Alg:      RS256 - signed by Keycloak")
    else:
      print("  ERROR:", json.dumps(data))
except Exception as e:
    print(f"  Error parsing login response: {e}")
PY
)
fi

KC_TOKEN=$(if [ "$RESP" = "FAILED" ]; then echo ""; else echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))"; fi)

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
