# ZTAM Platform — Zero Trust Application Middleware

> Wraps any existing web application behind Keycloak authentication, OPA policy enforcement,
> and automatic JWT token translation — **without touching the application's source code.**

```
Browser ──▶ Envoy :80 ──▶ Auth Middleware ──▶ OPA
                │                │
                │                └──▶ Keycloak (SPI reads app's own DB)
                │
                └──▶ TestApp :3000  (zero source changes)
```

---

## What's Inside

| Container | Image | Port | Role |
|---|---|---|---|
| `keycloak` | keycloak:26.3 | 8080 | Identity provider + session management |
| `postgres` | postgres:16 | — | Keycloak's internal storage |
| `opa` | openpolicyagent/opa:latest | 8181 | Policy engine (Rego rules) |
| `auth-middleware` | FastAPI / Python 3.12 | 8001 | JWT validation + token translation |
| `envoy` | envoyproxy/envoy:v1.28 | **80** | Reverse proxy + ext_authz enforcement |
| `testapp` | Node.js 20 (TaskManager) | 3001¹ | Demo app — source code untouched |
| `testapp-db` | mysql:8 | — | TestApp's user + task database |

> ¹ Port 3001 is a direct bypass for development only. **Always use port 80 in production.**

---

## Prerequisites

- Docker + Docker Compose v2
- Maven 3.9+ with JDK 17+ — needed once to build the Keycloak SPI JAR
- ~4 GB RAM available to Docker

---

## Quick Start

### 1 — Build the Keycloak SPI (one-time)

The SPI is a Java plugin that lets Keycloak authenticate users against TestApp's MySQL database.

```bash
cd keycloak-db-spi
mvn clean package -DskipTests
cd ..
```

Produces: `keycloak-db-spi/target/keycloak-db-spi.jar`

### 2 — Create `.env`

```dotenv
PG_PASS=ztam_secret_123
KC_ADMIN_PASS=admin_secret_456
KC_REALM=test-tenant
KC_CLIENT_ID=test-app
KC_CLIENT_SECRET=test-app-secret-2024
TESTAPP_JWT_SECRET=super_secret_jwt_key_for_ztam_demo_2024
```

### 3 — Start the stack

```bash
docker compose up -d --build
```

Wait ~30 s for Keycloak to finish initializing, then:

### 4 — Prime Keycloak (one-time)

Creates the realm, client, SPI registration, and JWT protocol mappers automatically:

```bash
python3 setup_demo.py
```

### 5 — Open the app

```
http://localhost:80
```

---

## Test Users

All users live in TestApp's MySQL DB. Keycloak reads and authenticates them via the SPI.

| Username | Password | Role | What they see |
|---|---|---|---|
| `alice` | `secret123` | **admin** | All tasks from all users |
| `charlie` | `pass123` | **user** | Only their own tasks |
| `testuser` | `test123` | **user** | Only their own tasks |
| `demouser` | `demo123` | **admin** | All tasks from all users |

---

## Exposed Ports

| URL | What |
|---|---|
| `http://localhost:80` | ✅ Main entry point — use this |
| `http://localhost:8080` | Keycloak Admin Console |
| `http://localhost:8001/health` | Auth Middleware health check |
| `http://localhost:8181` | OPA API (debug) |
| `http://localhost:3001` | TestApp direct — bypasses all auth |

**Keycloak admin login:** `admin` / `admin_secret_456`

---

## Login Flow (step by step)

1. Browser opens `http://localhost:80` → Envoy serves `login.html` with no auth check
2. User submits credentials → `POST /api/auth/login`
3. Envoy intercepts → rewrites destination to **auth-middleware** `/login-proxy`
4. auth-middleware calls Keycloak's token endpoint with the user credentials
5. Keycloak invokes the **Java SPI** → `SELECT` from TestApp's MySQL → verifies bcrypt hash
6. Keycloak returns an **RS256 JWT** (signed with Keycloak's RSA private key)
7. auth-middleware sends `{ token, username, role }` back — same shape as TestApp's own login
8. Browser stores the RS256 token; Keycloak creates a visible session

For every subsequent API call:

9. Browser sends `Authorization: Bearer <RS256 token>`
10. Envoy's `ext_authz` filter pauses the request → forwards to auth-middleware
11. auth-middleware validates the RS256 token against Keycloak's JWKS (cached 5 min)
12. auth-middleware calls OPA with `{ user, request, device }` → OPA allows or denies
13. On allow: auth-middleware mints an **HS256 token** (TestApp's own secret) → sends it back in the `authorization` header
14. Envoy forwards the request to TestApp with the HS256 token — TestApp works normally

See [ARCHITECTURE.md](ARCHITECTURE.md) for full diagrams.

---

## OPA Policy Summary

Rules are in `policies/authz.rego`. Permissions are in `policies/permissions.json` (hot-reloaded).

| Role | Access |
|---|---|
| **admin** | Full access to everything |
| **user** | GET + POST on `/api/*`, blocked from `/admin/*` |

**Device trust:** admin paths require device score ≥ 80 + encrypted=true. All other paths require score ≥ 60.

---

## Directory Structure

```
ztam-platform/
├── envoy/
│   └── envoy.yaml                  # Routing rules + ext_authz config
├── keycloak-db-spi/
│   └── src/main/java/com/ztam/spi/
│       ├── MySqlUserStorageProviderFactory.java
│       ├── MySqlUserStorageProvider.java   # SQL + bcrypt verify
│       └── MySqlUserAdapter.java            # Keycloak UserModel wrapper
├── policies/
│   ├── authz.rego                  # OPA policy (Rego)
│   └── permissions.json            # Per-role allowed/denied paths
├── services/
│   └── auth-middleware/
│       ├── main.py                 # /login-proxy + /check (ext_authz handler)
│       ├── requirements.txt
│       └── Dockerfile
├── testapp/                        # Demo app — git clone, untouched
├── docker-compose.yml
├── .env
├── setup_demo.py                   # One-time Keycloak priming script
└── ARCHITECTURE.md                 # Detailed component + flow documentation
```

---

## Useful Commands

```bash
# Restart a single service after config changes
docker compose restart envoy
docker compose up -d --build auth-middleware

# Follow logs for a specific service
docker compose logs -f auth-middleware
docker compose logs -f envoy

# Wipe everything (including DB data)
docker compose down -v

# Quick end-to-end test
TOKEN=$(curl -s -X POST http://localhost:80/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123"}' | jq -r .token)

curl -s http://localhost:80/api/tasks \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## Security Properties

| Property | Mechanism |
|---|---|
| Authentication | RS256 JWT validated against Keycloak JWKS on every request |
| Authorization | OPA Rego: role + device score checked before every API call |
| Fail-closed | `failure_mode_allow: false` — auth service down = all traffic denied |
| Read-only DB access | SPI issues `SELECT` only — never writes to app's MySQL |
| Token translation | RS256 → HS256 translation lets app work with its own existing JWT secret |
| JWKS caching | 5-minute in-memory cache — JWKS endpoint never becomes a bottleneck |
| Session visibility | Every login creates a real Keycloak session — visible in Admin Console |
