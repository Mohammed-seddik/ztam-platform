#!/usr/bin/env python3
"""Setup and run the full ZTAM demo."""
import json, base64, os, urllib.request, urllib.parse

KC            = os.environ.get("KEYCLOAK_URL",   "http://localhost:8080")
REALM         = os.environ.get("KC_REALM",       "test-tenant")
KC_ADMIN_PASS = os.environ["KC_ADMIN_PASS"]
KC_CLIENT_SECRET = os.environ["KC_CLIENT_SECRET"]


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
            return r.getcode(), json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


# --- 1. Get admin token ---
_, d = kc("POST", "/realms/master/protocol/openid-connect/token", {
    "client_id": "admin-cli", "grant_type": "password",
    "username": "admin", "password": KC_ADMIN_PASS
}, form=True)
if "access_token" not in d:
    print(f"ERROR: could not obtain admin token: {d}")
    raise SystemExit(1)
ADMIN = d["access_token"]

# --- 2. Delete ALL native Keycloak users (force SPI to serve them) ---
_, users = kc("GET", f"/admin/realms/{REALM}/users?max=50", token=ADMIN)
deleted = []
for u in (users if isinstance(users, list) else []):
    if not u.get("federationLink"):
        code, _ = kc("DELETE", f"/admin/realms/{REALM}/users/{u['id']}", token=ADMIN)
        deleted.append((u["username"], code))

print("=== Deleted native Keycloak users ===")
for name, code in deleted:
    print(f"  {name} -> HTTP {code}")
if not deleted:
    print("  None (already clean)")

# --- 3. Login alice via Keycloak (SPI reads from testapp-db) ---
_, resp = kc("POST", f"/realms/{REALM}/protocol/openid-connect/token", {
    "grant_type": "password", "client_id": "test-app",
    "client_secret": KC_CLIENT_SECRET,
    "username": "alice", "password": "secret123"
}, form=True)

print("\n=== LOGIN alice via Keycloak+SPI+testapp-db ===")
if "access_token" in resp:
    tok = resp["access_token"]
    raw = tok.split(".")[1]
    padding = (4 - len(raw) % 4) % 4
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * padding))
    print(f"  Status:   SUCCESS")
    print(f"  Username: {payload.get('preferred_username')}")
    print(f"  Issuer:   {payload.get('iss')}")
    print(f"  Role:     {payload.get('role', '(role claim not yet in token)')}")
    print(f"  Alg:      RS256 - Keycloak signed")
    print(f"  Token[:60]: {tok[:60]}...")
    KC_TOKEN = tok
else:
    print(f"  ERROR: {resp}")
    KC_TOKEN = None

# --- 4. Show who's now in Keycloak (should be federation/SPI users) ---
_, users_after = kc("GET", f"/admin/realms/{REALM}/users?max=50", token=ADMIN)
print("\n=== Keycloak users after SPI login ===")
for u in (users_after if isinstance(users_after, list) else []):
    src = "FEDERATION(SPI->testapp-db)" if u.get("federationLink") else "NATIVE"
    print(f"  {u.get('username'):15} {src}")

print("\n=== Token sources comparison ===")
print("  TestApp JWT:  alg=HS256, iss=(none), signed by app's JWT_SECRET")
print("  Keycloak JWT: alg=RS256, iss=http://localhost:8080/realms/test-tenant")
print("\n=== FLOW ===")
print("  1. User enters username+password")
print("  2. Keycloak SPI queries: SELECT * FROM taskapp.users WHERE username=?")
print("  3. SPI reads password_hash, verifies bcrypt")
print("  4. Keycloak issues RS256 JWT (NEVER writes to testapp-db)")
print("  5. Every request goes through Envoy -> auth-middleware -> OPA")
print("  6. app on port 3000 receives request only if OPA says allow=true")
