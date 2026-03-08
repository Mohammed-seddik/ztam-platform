#!/usr/bin/env python3
"""
ZTAM Platform — One-shot Keycloak setup + smoke test.

Idempotent: safe to run multiple times.
Reads credentials from .env (in project root) then from environment.

Steps:
  1.  Load .env
  2.  Get Keycloak admin token
  3.  Create realm  (test-tenant)
  4.  Create client (test-app, confidential, directAccessGrants)
  5.  Set client secret
  6.  Register MySQL SPI user-federation component
  7.  Create protocol mappers  (role, db_user_id)
  8.  Delete any native Keycloak users (force SPI)
  9.  Smoke-test login as alice
  10. Print summary
"""
import json, base64, os, sys, urllib.request, urllib.parse
from pathlib import Path

# ── CLI flag: --force enables native user deletion ─────────────────────────────
FORCE_MODE = "--force" in sys.argv

# ── Load .env from project root ────────────────────────────────────────────────
_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

# ── Config ─────────────────────────────────────────────────────────────────────
KC               = os.environ.get("KEYCLOAK_URL",    "http://localhost:8080")
REALM            = os.environ.get("KC_REALM",        "test-tenant")
KC_ADMIN_USER    = os.environ.get("KC_ADMIN_USER",   "admin")
KC_ADMIN_PASS    = os.environ.get("KC_ADMIN_PASS",   "")
KC_CLIENT_ID     = os.environ.get("KC_CLIENT_ID",    "test-app")
KC_CLIENT_SECRET = os.environ.get("KC_CLIENT_SECRET","")
MYSQL_HOST       = os.environ.get("DB_HOST",         "testapp-db")
MYSQL_PORT       = os.environ.get("DB_PORT",         "3306")
MYSQL_DB         = os.environ.get("MYSQL_DATABASE",  "taskapp")
MYSQL_USER       = os.environ.get("MYSQL_USER",      "")
MYSQL_PASS       = os.environ.get("MYSQL_PASSWORD",  "")

for _var, _val in (
    ("KC_ADMIN_PASS",    KC_ADMIN_PASS),
    ("KC_CLIENT_SECRET", KC_CLIENT_SECRET),
    ("MYSQL_USER",       MYSQL_USER),
    ("MYSQL_PASSWORD",   MYSQL_PASS),
):
    if not _val:
        print(f"ERROR: required variable {_var!r} is not set in .env")
        sys.exit(1)


# ── HTTP helper ────────────────────────────────────────────────────────────────
def kc(method, path, data=None, token=None, form=False):
    url = KC + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data and form:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.getcode(), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


def step(n, msg):
    print(f"\n[{n}] {msg}")


# ── 1. Admin token ─────────────────────────────────────────────────────────────
step(1, "Authenticating with Keycloak admin...")
code, d = kc("POST", "/realms/master/protocol/openid-connect/token", {
    "client_id": "admin-cli", "grant_type": "password",
    "username": KC_ADMIN_USER, "password": KC_ADMIN_PASS,
}, form=True)
if "access_token" not in d:
    print(f"   ERROR: could not obtain admin token (HTTP {code}): {d}")
    print("   Is Keycloak running?  docker compose up -d")
    sys.exit(1)
ADMIN = d["access_token"]
print("   ✓ Admin authenticated")


# ── 2. Create realm ────────────────────────────────────────────────────────────
step(2, f"Ensuring realm '{REALM}' exists...")
code, _ = kc("POST", "/admin/realms", {
    "realm": REALM,
    "enabled": True,
    "displayName": "ZTAM Demo",
    "registrationAllowed": False,
    "loginTheme": "keycloak",
    "accessTokenLifespan": 3600,
    "ssoSessionMaxLifespan": 36000,
}, token=ADMIN)
if code == 201:
    print(f"   ✓ Realm '{REALM}' created")
elif code == 409:
    print(f"   ✓ Realm '{REALM}' already exists — skipping creation")
else:
    print(f"   ERROR: realm creation returned HTTP {code}")
    sys.exit(1)


# ── 3. Create client ───────────────────────────────────────────────────────────
step(3, f"Ensuring client '{KC_CLIENT_ID}' exists...")
code, _ = kc("POST", f"/admin/realms/{REALM}/clients", {
    "clientId":                 KC_CLIENT_ID,
    "name":                     KC_CLIENT_ID,
    "enabled":                  True,
    "protocol":                 "openid-connect",
    "publicClient":             False,
    "directAccessGrantsEnabled": True,
    "standardFlowEnabled":      True,
    "serviceAccountsEnabled":   False,
    "secret":                   KC_CLIENT_SECRET,
}, token=ADMIN)
if code == 201:
    print(f"   ✓ Client '{KC_CLIENT_ID}' created")
elif code == 409:
    print(f"   ✓ Client '{KC_CLIENT_ID}' already exists — skipping creation")
else:
    print(f"   ERROR: client creation returned HTTP {code}")
    sys.exit(1)

# Get client UUID (needed for sub-resource API calls)
code, clients = kc("GET",
    f"/admin/realms/{REALM}/clients?clientId={KC_CLIENT_ID}&max=1",
    token=ADMIN)
if not clients:
    print("   ERROR: could not resolve client UUID")
    sys.exit(1)
CLIENT_UUID = clients[0]["id"]
print(f"   ✓ Client UUID: {CLIENT_UUID}")

# ── Ensure client secret matches .env ─────────────────────────────────────────
code, _ = kc("PUT",
    f"/admin/realms/{REALM}/clients/{CLIENT_UUID}/client-secret",
    {"type": "secret", "value": KC_CLIENT_SECRET},
    token=ADMIN)
# 204 = updated, 200 = already correct
if code in (200, 204):
    print("   ✓ Client secret synchronized")


# ── 4. Register MySQL User-Federation SPI ─────────────────────────────────────
step(4, "Registering MySQL SPI user-federation component...")
code, components = kc("GET",
    f"/admin/realms/{REALM}/components?type=org.keycloak.storage.UserStorageProvider",
    token=ADMIN)
existing_spi = next(
    (c for c in (components if isinstance(components, list) else [])
     if c.get("providerId") == "mysql-db-provider"), None)

if existing_spi:
    print("   ✓ MySQL SPI already registered — skipping")
else:
    code, _ = kc("POST",
        f"/admin/realms/{REALM}/components",
        {
            "name":        "testapp-db",
            "providerId":  "mysql-db-provider",
            "providerType": "org.keycloak.storage.UserStorageProvider",
            "parentId":    REALM,
            "config": {
                "db_host":      [MYSQL_HOST],
                "db_port":      [MYSQL_PORT],
                "db_name":      [MYSQL_DB],
                "db_user":      [MYSQL_USER],
                "db_pass":      [MYSQL_PASS],
                "table_name":   ["users"],
                "username_col": ["username"],
                "password_col": ["password_hash"],
                "role_col":     ["role"],
                "cachePolicy":  ["DEFAULT"],
            },
        },
        token=ADMIN)
    if code == 201:
        print("   ✓ MySQL SPI component registered")
    else:
        print(f"   ERROR: SPI registration returned HTTP {code}")
        print("   Make sure the SPI JAR is built: cd keycloak-db-spi && mvn clean package")
        sys.exit(1)


# ── 5. Create protocol mappers ────────────────────────────────────────────────
step(5, "Creating protocol mappers (role, db_user_id)...")

MAPPERS_TO_CREATE = [
    {
        "name":            "ztam-role",
        "protocol":        "openid-connect",
        "protocolMapper":  "oidc-usermodel-attribute-mapper",
        "consentRequired": False,
        "config": {
            "user.attribute":      "role",
            "claim.name":          "role",
            "jsonType.label":      "String",
            "id.token.claim":      "true",
            "access.token.claim":  "true",
            "userinfo.token.claim":"true",
            "aggregate.attrs":     "false",
            "multivalued":         "false",
        },
    },
    {
        "name":            "ztam-db-user-id",
        "protocol":        "openid-connect",
        "protocolMapper":  "oidc-usermodel-attribute-mapper",
        "consentRequired": False,
        "config": {
            "user.attribute":      "db_user_id",
            "claim.name":          "db_user_id",
            "jsonType.label":      "String",
            "id.token.claim":      "true",
            "access.token.claim":  "true",
            "userinfo.token.claim":"true",
            "aggregate.attrs":     "false",
            "multivalued":         "false",
        },
    },
]

code, existing_mappers = kc("GET",
    f"/admin/realms/{REALM}/clients/{CLIENT_UUID}/protocol-mappers/models",
    token=ADMIN)
existing_names = {m["name"] for m in (existing_mappers if isinstance(existing_mappers, list) else [])}

for mapper in MAPPERS_TO_CREATE:
    if mapper["name"] in existing_names:
        print(f"   ✓ Mapper '{mapper['name']}' already exists — skipping")
        continue
    code, _ = kc("POST",
        f"/admin/realms/{REALM}/clients/{CLIENT_UUID}/protocol-mappers/models",
        mapper, token=ADMIN)
    if code == 201:
        print(f"   ✓ Mapper '{mapper['name']}' created")
    else:
        print(f"   WARNING: mapper '{mapper['name']}' creation returned HTTP {code}")


# ── 6. Delete native Keycloak users (force SPI to serve them) ─────────────────
step(6, "Removing any native Keycloak users (forces SPI federation)...")
if not FORCE_MODE:
    print("   ⚠  Skipping native user deletion (pass --force to delete).")
    print("      Run: python3 demo/setup_demo.py --force")
else:
    code, users = kc("GET", f"/admin/realms/{REALM}/users?max=100", token=ADMIN)
    deleted = []
    for u in (users if isinstance(users, list) else []):
        if not u.get("federationLink"):
            c, _ = kc("DELETE", f"/admin/realms/{REALM}/users/{u['id']}", token=ADMIN)
            deleted.append((u.get("username", "?"), c))
    if deleted:
        for name, c in deleted:
            print(f"   Deleted native user '{name}' → HTTP {c}")
    else:
        print("   ✓ No native users found (already clean)")


# ── 7. Smoke-test: login as alice ─────────────────────────────────────────────
step(7, "Smoke test — login alice via Keycloak + SPI + testapp-db...")
code, resp = kc("POST", f"/realms/{REALM}/protocol/openid-connect/token", {
    "grant_type": "password",
    "client_id":  KC_CLIENT_ID,
    "client_secret": KC_CLIENT_SECRET,
    "username": "alice",
    "password": "secret123",
}, form=True)

if "access_token" in resp:
    tok = resp["access_token"]
    raw = tok.split(".")[1]
    padding = (4 - len(raw) % 4) % 4
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * padding))
    print(f"   ✓ Login SUCCESS")
    print(f"     preferred_username : {payload.get('preferred_username')}")
    print(f"     role               : {payload.get('role', '(missing — check SPI + mapper)')}")
    print(f"     db_user_id         : {payload.get('db_user_id', '(missing)')}")
    print(f"     iss                : {payload.get('iss')}")
    print(f"     alg                : RS256 (Keycloak-signed)")
    if not payload.get("role"):
        print("   WARNING: 'role' claim missing. Verify protocol mapper + SPI registration.")
else:
    print(f"   ERROR: login failed (HTTP {code}): {resp}")
    print("   Possible causes:")
    print("   - testapp-db not yet initialized (wait 10s, retry)")
    print("   - SPI JAR not built: cd keycloak-db-spi && mvn clean package -DskipTests")
    print("   - Wrong KC_CLIENT_SECRET in .env")

# ── 8. Summary ────────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
print("  ZTAM Keycloak setup complete")
print("═" * 60)
print(f"  Realm            : {REALM}")
print(f"  Client           : {KC_CLIENT_ID}")
print(f"  User federation  : MySQL SPI → {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
print(f"  Protocol mappers : role, db_user_id")
print(f"  Admin console    : http://localhost:8080")
print()
print("  Test users (in testapp-db):")
print("    alice      / secret123 → admin")
print("    charlie    / pass123   → user")
print("    testuser   / test123   → user")
print("    demouser   / demo123   → admin")
print()
print("  Open the app:  https://localhost")
print("═" * 60)
