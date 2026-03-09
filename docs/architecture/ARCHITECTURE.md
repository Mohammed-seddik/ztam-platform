# ZTAM Platform — Architecture

This document explains every component, how they connect, and the exact flow of each request.

---

## High-Level Overview

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                   Docker Network: ztam                   │
                         │                                                          │
                         │  ┌───────────────────┐  ext_authz  ┌──────────────────┐ │
Browser ─ HTTPS :443 ───►│  │  Envoy  v1.28     │────────────►│ Auth Middleware  │ │
Browser ─ HTTP  :80  ───►│  │  TLS termination  │◄────────────│ FastAPI (intern) │ │
  (→ 301 to HTTPS)       │  │  security headers │ 200/401/403 │                  │ │
                         │  └────────┬──────────┘             │ 1. Validate RS256│ │
                         │           │ (on allow)              │ 2. Rate limit IP │ │
                         │           │ forward + HS256 token   │ 3. Call OPA      │ │
                         │           ▼                         │ 4. Mint HS256    │ │
                         │  ┌──────────────┐           ┌───────┴────────┐         │ │
                         │  │   TestApp    │           │   Keycloak     │         │ │
                         │  │   Node.js    │           │   :8080 (admin)│         │ │
                         │  │  (internal)  │           │  SPI ─────────►│MySQL)   │ │
                         │  └──────────────┘           └───────┬────────┘         │ │
                         │                             ┌───────┴────────┐         │ │
                         │                             │  OPA (internal)│         │ │
                         │                             └────────────────┘         │ │
                         └──────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Envoy Proxy (`envoy/envoy.yaml`)

**Role:** Policy Enforcement Point (PEP). Every byte of traffic goes through Envoy.

**Key responsibilities:**

- Listens on port 80 — returns HTTP 301 redirect to HTTPS
- Listens on port 443 (TLS 1.2/1.3, ECDHE ciphers only, cert in `envoy/certs/`)
- Enforces `ext_authz` — pauses each request and asks auth-middleware for a decision
- Adds security response headers on every HTTPS response:
  - `Strict-Transport-Security`, `Content-Security-Policy`, `X-Content-Type-Options`,
    `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`
- Injects headers returned by auth-middleware into forwarded requests
- **`failure_mode_allow: false`** — if auth-middleware is unreachable, all requests are denied

**Route table (in priority order, any method unless noted):**

| Match                     | Destination                      | ext_authz                              |
| ------------------------- | -------------------------------- | -------------------------------------- |
| `prefix /ztam/`           | Auth Middleware (platform login) | **disabled** — serves ZTAM login page  |
| `path /`                  | TestApp                          | **disabled** (serve login page freely) |
| `path /login.html`        | TestApp                          | **disabled**                           |
| `prefix /dashboard.html`  | TestApp                          | **disabled**                           |
| `prefix /register.html`   | TestApp                          | **disabled**                           |
| `path /api/auth/login`    | Auth Middleware (`/login-proxy`) | **disabled**                           |
| `path /api/auth/register` | TestApp                          | **disabled**                           |
| `path /api/auth/logout`   | Auth Middleware (`/logout`)      | **disabled**                           |
| Everything else           | TestApp                          | **ENABLED** ← enforced                 |

---

### 2. Auth Middleware (`services/auth-middleware/main.py`)

**Role:** The brain. Validates every token and decides allow/deny.

**Two endpoints:**

#### `POST /login-proxy`

Called by Envoy for `POST /api/auth/login`. Does NOT validate a token — it IS the login.

```
Request body: { "username": "alice", "password": "$DEMO_ALICE_PASSWORD" }

0. Reject if IP has exceeded 10 attempts in 60 s  → 429
1. Reject if username > 200 chars or password > 1000 chars  → 400
2. Calls Keycloak token endpoint with grant_type=password
3. Keycloak → SPI → MySQL → bcrypt verify
4. Keycloak returns RS256 JWT
5. Extracts claims: preferred_username, role, db_user_id
6. Returns: { "token": "<KC RS256 token>", "username": "alice", "role": "admin" }
```

The token returned is the **Keycloak RS256 token** — not HS256 — so that all subsequent API
calls can be validated against Keycloak's JWKS.

#### `/{full_path}` (catch-all — ext_authz handler)

Called by Envoy for every other request. Validates the incoming token and decides allow/deny.

```
1. Extract Bearer token from Authorization header OR ztam_auth cookie  →  401/redirect if missing
2. Fetch Keycloak JWKS (cached 5 min, lock-protected)
3. Validate RS256 signature + expiry + issuer                          →  403 if invalid
4. Extract: sub, email, roles, tenant_id, azp
   (device trust is intentionally deferred to Phase 2)
5. POST to OPA /v1/data/authz  (single call — returns both allow + deny_reason)
6. If OPA allows:
     - Mint HS256 token with TestApp's JWT_SECRET
     - Return 200 + headers: x-user-id, x-user-roles, x-tenant-id, authorization: Bearer <HS256>
7. If OPA denies:
     - Return 403 { "error": "access denied", "reason": "<deny_reason from same OPA response>" }
```

---

### 3. Keycloak (`keycloak:26.5.5`)

**Role:** Identity Provider. Issues and signs RS256 JWTs.

**Configuration for this demo:**

- Realm: `test-tenant`
- Client: `test-app` (confidential, client-secret from `KC_CLIENT_SECRET`)
- User Federation: MySQL DB Provider (the custom Java SPI)
- Protocol Mappers:
  - `role` attribute → JWT claim `role`
  - `db_user_id` attribute → JWT claim `db_user_id`

Keycloak's own database is PostgreSQL (`postgres` container, internal only).

---

### 4. Java SPI (`keycloak-db-spi/`)

**Role:** Bridges Keycloak to TestApp's MySQL. Keycloak has no knowledge of MySQL by default.

**Three classes:**

| Class                             | Role                                                                                        |
| --------------------------------- | ------------------------------------------------------------------------------------------- |
| `MySqlUserStorageProviderFactory` | Registers the provider, defines config UI fields in Keycloak Admin                          |
| `MySqlUserStorageProvider`        | Executes SQL queries, verifies passwords, returns UserModel                                 |
| `MySqlUserAdapter`                | Wraps a result row as a Keycloak `UserModel`, exposes `role` and `db_user_id` as attributes |

**SQL used (column names are configurable in the Keycloak Admin UI):**

```sql
-- User lookup:
SELECT `id`, `username`, `role` FROM `users` WHERE `username` = ? LIMIT 1

-- Password hash fetch (separate query during credential validation):
SELECT `password_hash` FROM `users` WHERE `username` = ? LIMIT 1
```

> Default column-name settings in the SPI Admin UI differ from the demo schema.
> `setup_demo.py` explicitly passes `username_col=username`, `password_col=password_hash`,
> `role_col=role` when registering the SPI component so the defaults don't matter for the demo.

**Password verification:**

- Reads bcrypt hash from `password_col`
- Normalizes `$2b$` prefix to `$2a$` (Node.js generates `$2b$`, Java's jBCrypt expects `$2a$`)
- Calls `BCrypt.checkpw(plaintext, normalizedHash)`

**Attributes exposed to Keycloak:**

- `role` — the value of `role_col` (e.g. `admin`, `user`)
- `db_user_id` — the integer primary key from the `id` column

These become JWT claims via the protocol mappers configured in `setup_demo.py`.

---

### 5. OPA (`policies/authz.rego`)

**Role:** Policy Decision Point. Evaluates rules against `{ user, request, device }`.

**Input shape (Phase 1 — device trust is Phase 2):**

```json
{
  "input": {
    "user": {
      "id": "abc123",
      "email": "alice@example.com",
      "roles": ["admin"],
      "tenant_id": "test-tenant"
    },
    "request": {
      "path": "/api/tasks",
      "method": "GET"
    }
  }
}
```

**Decision logic (`authz.rego`) — Phase 1:**

```
allow = true  iff  user.id != ""  AND  roles not empty  AND  role_permitted

role_permitted:
  - "admin" in roles  → always true (full access)
  - other roles       → look up tenant permissions (tenants.json) or global defaults (permissions.json):
                        path matches allowed_paths
                        AND method in allowed_methods
                        AND path NOT in denied_paths

# device_ok is intentionally excluded from Phase 1.
# Phase 2 will add real device trust-store integration.
```

**Permissions are in `policies/permissions.json`** — OPA hot-reloads this file, no restart needed.

---

### 6. TestApp (`demo/testapp/`)

**Role:** The demo application being protected. Node.js task manager with MySQL backend.

**It was never modified.** Its original code:

- Expects `Authorization: Bearer <HS256 token>` signed with `JWT_SECRET`
- Uses `user_id` (integer FK) in the tasks table

ZTAM handles both requirements transparently:

- Token translation: auth-middleware mints HS256 with TestApp's `JWT_SECRET`
- User ID: the `db_user_id` claim from the SPI is used as the `sub` in the HS256 token

---

## Full Request Flows

### Flow A: Opening the App in a Browser

```
Browser                  Envoy                 TestApp
  │                        │                      │
  │── GET / ──────────────►│                      │
  │                        │ (route: path="/")     │
  │                        │ ext_authz: disabled   │
  │                        │── GET / ─────────────►│
  │                        │◄── 200 login.html ───│
  │◄── 200 login.html ────│                      │
```

### Flow B: Logging In

```
Browser          Envoy           Auth Middleware        Keycloak          MySQL
  │                │                    │                   │                │
  │─ POST          │                    │                   │                │
  │  /api/auth/   │                    │                   │                │
  │  login ──────►│                    │                   │                │
  │               │ prefix_rewrite     │                   │                │
  │               │ → /login-proxy     │                   │                │
  │               │──────────────────►│                   │                │
  │               │                   │─ POST token ──────►│                │
  │               │                   │  grant_type=password│               │
  │               │                   │                   │─ SQL SELECT ──►│
  │               │                   │                   │◄── bcrypt hash─│
  │               │                   │                   │  verify OK      │
  │               │                   │◄─ RS256 JWT ──────│                │
  │               │                   │                   │                │
  │               │◄─ {token,user,role}│                   │                │
  │◄─ 200 ────────│                   │                   │                │
  │  {token,       │                   │                   │                │
  │   username,    │                   │                   │                │
  │   role}        │                   │                   │                │
```

### Flow C: Authenticated API Request

```
Browser     Envoy        Auth Middleware       OPA          TestApp
  │            │                 │               │              │
  │─ GET       │                 │               │              │
  │  /api/tasks│                 │               │              │
  │  Bearer    │                 │               │              │
  │  RS256 ───►│                 │               │              │
  │            │── ext_authz ───►│               │              │
  │            │   (check RS256) │               │              │
  │            │                 │─ validate sig  │              │
  │            │                 │  via JWKS      │              │
  │            │                 │               │              │
  │            │                 │─ POST /v1/data/authz ─────────►│
  │            │                 │◄─ { result: {allow:true} } ──│
  │            │                 │               │              │
  │            │                 │  mint HS256 token            │
  │            │◄── 200 ────────│               │              │
  │            │   authorization: Bearer <HS256> │              │
  │            │                 │               │              │
  │            │── GET /api/tasks ──────────────────────────────►│
  │            │   Authorization: Bearer <HS256> │              │
  │            │◄─────────────────────────────────────── 200 ──│
  │◄─ 200 ────│                 │               │              │
```

### Flow D: Denied Request (wrong role)

```
Browser     Envoy        Auth Middleware       OPA
  │            │                 │               │
  │─ GET       │                 │               │
  │  /admin/   │                 │               │
  │  Bearer    │                 │               │
  │  RS256 ───►│                 │               │
  │            │── ext_authz ───►│               │
  │            │                 │─ POST /v1/data/authz ─────────►│
  │            │                 │  (role=user, path=/admin/)       │
  │            │                 │◄─ {allow:false,                  │
  │            │                 │    deny_reason:"role does not..."}│
  │            │◄── 403 ────────│               │
  │            │  {"error":"access denied",      │
  │            │   "reason":"role does not..."}  │
  │◄─ 403 ────│                 │               │
```

---

## Token Translation Detail

TestApp was written expecting **HS256** tokens signed with `JWT_SECRET` from environment.

Keycloak issues **RS256** tokens signed with its own RSA key pair.

The translation happens in auth-middleware after OPA allows the request:

```python
# Claims extracted from the Keycloak RS256 token:
db_user_id = claims.get("db_user_id")   # integer PK from MySQL (set by SPI)
username   = claims.get("preferred_username")
role       = claims.get("role")          # custom claim set by SPI

# New HS256 token for TestApp:
downstream_payload = {
    "sub":      db_user_id,   # TestApp uses this as user_id FK in tasks table
    "username": username,
    "role":     role,
    "iat":      now,
    "exp":      now + 3600,
}
downstream_token = jwt.encode(downstream_payload, TESTAPP_JWT_SECRET, algorithm="HS256")
```

This token is sent back to Envoy in the `authorization` header, which then forwards it to TestApp.
TestApp receives exactly what it would receive if you called it directly — it has no idea ZTAM exists.

---

## Environment Variables Reference

### `auth-middleware`

| Variable             | Description                                               |
| -------------------- | --------------------------------------------------------- |
| `KEYCLOAK_URL`       | Keycloak base URL (internal Docker)                       |
| `KC_REALM`           | Realm name                                                |
| `KC_CLIENT_ID`       | Keycloak client ID                                        |
| `KC_CLIENT_SECRET`   | Client secret — **required, no default**                  |
| `OPA_URL`            | OPA base URL                                              |
| `TESTAPP_JWT_SECRET` | TestApp's HS256 signing secret — **required, no default** |

> auth-middleware will **refuse to start** if `KC_CLIENT_SECRET` or `TESTAPP_JWT_SECRET` are empty.

### `testapp`

| Variable     | Description                           |
| ------------ | ------------------------------------- |
| `DB_HOST`    | MySQL host (`testapp-db`)             |
| `DB_NAME`    | Database name (`taskapp`)             |
| `JWT_SECRET` | Must match `TESTAPP_JWT_SECRET` above |

---

## Key Design Decisions

### Why ext_authz and not a sidecar?

Envoy's `ext_authz` sends the full original request headers to auth-middleware before passing
the request upstream. This means auth-middleware sees the real path, method, and Authorization
header — it can make a correct decision without sniffing traffic.

### Why token translation instead of teaching TestApp about RS256?

The goal is zero source changes to the protected app. TestApp already has JWT middleware
that works with HS256 + its own secret. Rather than adding RS256 support to TestApp (which
would require code changes + a redeploy), auth-middleware mints an HS256 token that TestApp
naturally accepts. From TestApp's perspective nothing changed.

### Why does the SPI expose `db_user_id`?

TestApp stores tasks with a `user_id` foreign key that maps to the integer `id` column in the
`users` table. Keycloak's default `sub` claim is a UUID. If we used that as `sub` in the
downstream HS256 token, TestApp's `WHERE user_id = ?` queries would fail (type mismatch).
The SPI reads and exposes the actual integer PK so the downstream token carries the exact
value TestApp's SQL expects.

### Why `$2b$` → `$2a$` normalization?

Node.js's `bcryptjs` library generates hashes prefixed with `$2b$`. Java's `jBCrypt` library
only recognizes `$2a$`. They are identical algorithms — the prefix difference is a historical
convention. The SPI normalizes the prefix before calling `BCrypt.checkpw()`.
