# ZTAM Platform — Zero Trust Application Middleware

> Wraps any existing web application behind Keycloak authentication, OPA policy enforcement,
> and automatic JWT token translation — **without touching the application's source code.**

```
Browser ─── HTTPS :443 ──▶ Envoy (TLS termination)
                │                │
                │                ├──▶ Auth Middleware  (internal only)
                │                │         │
                │                │         ├──▶ Keycloak JWKS (RS256 validation)
                │                │         ├──▶ OPA (policy decision)
                │                │         └──▶ Keycloak token endpoint (login)
                │
                └──▶ TestApp :3000  (internal only — zero source changes)

HTTP :80 → 301 redirect → HTTPS :443
```

---

## What's Inside

| Container         | Image                      | Exposed Port      | Role                                               |
| ----------------- | -------------------------- | ----------------- | -------------------------------------------------- |
| `envoy`           | envoyproxy/envoy:v1.28.7   | **80, 443**       | TLS termination + reverse proxy + ext_authz        |
| `keycloak`        | keycloak:26.5.5            | 8080 (admin only) | Identity provider + session management             |
| `auth-middleware` | FastAPI / Python 3.12      | — (internal)      | JWT validation + rate limiting + token translation |
| `opa`             | openpolicyagent/opa:0.64.1 | — (internal)      | Policy engine (Rego rules)                         |
| `testapp`         | Node.js 20                 | — (internal)      | Protected app — source code untouched              |
| `testapp-db`      | mysql:8                    | — (internal)      | TestApp's user + task database                     |
| `postgres`        | postgres:16                | — (internal)      | Keycloak's internal storage                        |

> All internal services are **not reachable from outside Docker** — only Envoy (443/80) and Keycloak admin (8080) are exposed. This enforces the Zero Trust perimeter.

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

### 2 — Generate TLS certificate (one-time)

A self-signed certificate is needed for Envoy's HTTPS listener:

```bash
bash envoy/generate-certs.sh
```

For production, replace `envoy/certs/server.crt` and `envoy/certs/server.key` with a
certificate signed by a trusted CA (Let's Encrypt, etc.).

### 3 — Create `.env`

Copy the example and fill in strong, random values:

```bash
cp .env.example .env
```

```dotenv
PG_PASS=<random>
KC_ADMIN_USER=admin
KC_ADMIN_PASS=<random>
KC_REALM=test-tenant
KC_CLIENT_ID=test-app
KC_CLIENT_SECRET=<random-32-chars>
MYSQL_ROOT_PASSWORD=<random>
MYSQL_DATABASE=taskapp
MYSQL_USER=<random>
MYSQL_PASSWORD=<random>
TESTAPP_JWT_SECRET=<random-64-chars>
```

> **Never commit `.env`.** It is in `.gitignore`.

### 4 — Start the stack

```bash
docker compose up -d --build
```

> **Development vs Production:**
> The `keycloak` service runs with `command: start-dev` by default.
> `start-dev` is suitable for local testing only.
> For a production deployment, change it to `start` and provide proper
> `KC_HOSTNAME`, TLS certificates, and firewall rules to restrict port 8080.

Wait ~30 s for Keycloak to finish initializing, then:

### 5 — Prime Keycloak (one-time)

Automatically creates the realm, Keycloak client, MySQL SPI registration,
and JWT protocol mappers, then smoke-tests the full login flow:

```bash
python3 setup_demo.py
```

### 6 — Open the app

```
https://localhost
```

HTTP requests to `http://localhost` are automatically redirected to HTTPS.

---

## Test Users

All users live in TestApp's MySQL DB. Keycloak reads and authenticates them via the SPI.

| Username   | Password    | Role      | What they see            |
| ---------- | ----------- | --------- | ------------------------ |
| `alice`    | `secret123` | **admin** | All tasks from all users |
| `charlie`  | `pass123`   | **user**  | Only their own tasks     |
| `testuser` | `test123`   | **user**  | Only their own tasks     |
| `demouser` | `demo123`   | **admin** | All tasks from all users |

---

## Exposed Ports

| URL                     | What                           |
| ----------------------- | ------------------------------ |
| `https://localhost`     | ✅ Main entry point — use this |
| `http://localhost`      | Redirects → HTTPS (301)        |
| `http://localhost:8080` | Keycloak Admin Console         |

**Keycloak admin login:** `admin` / `<KC_ADMIN_PASS from .env>`

OPA, auth-middleware, and testapp are **not** exposed externally — Zero Trust perimeter.

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

| Role       | Methods                       | Paths                              |
| ---------- | ----------------------------- | ---------------------------------- |
| **admin**  | All                           | All                                |
| **editor** | GET, POST, PUT, PATCH, DELETE | `/api/*` — blocked from `/admin/*` |
| **user**   | GET, POST, PUT, PATCH, DELETE | `/api/*` — blocked from `/admin/*` |
| **viewer** | GET                           | `/api/*` — blocked from `/admin/*` |

**Device trust:** admin paths require device score ≥ 80 + encrypted=true. All other paths require score ≥ 60.

---

## Directory Structure

```
ztam-platform/
├── envoy/
│   ├── envoy.yaml              # Routing, TLS, ext_authz, security headers
│   ├── generate-certs.sh       # Self-signed TLS cert generator
│   └── certs/                  # server.crt + server.key (not in git)
├── keycloak-db-spi/
│   └── src/main/java/com/ztam/spi/
│       ├── MySqlUserStorageProviderFactory.java
│       ├── MySqlUserStorageProvider.java   # SQL + bcrypt verify
│       └── MySqlUserAdapter.java           # Keycloak UserModel wrapper
├── policies/
│   ├── authz.rego              # OPA policy (Rego)
│   ├── authz_test.rego         # OPA unit tests (14 tests)
│   └── permissions.json        # Per-role allowed methods + paths
├── services/
│   └── auth-middleware/
│       ├── main.py              # /login-proxy + ext_authz handler + rate limiter
│       ├── requirements.txt
│       └── Dockerfile
├── testapp/                    # Protected app — source code untouched
├── docker-compose.yml
├── .env                        # Secrets — never committed
├── .env.example
├── setup_demo.py               # One-time Keycloak priming script
└── ARCHITECTURE.md             # Detailed component + flow documentation
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

# Run OPA policy unit tests
docker run --rm -v ./policies:/policies openpolicyagent/opa:0.64.1-debug test /policies -v

# Wipe everything (including DB data)
docker compose down -v

# Quick end-to-end test (HTTPS)
TOKEN=$(curl -sk -X POST https://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123"}' | jq -r .token)

curl -sk https://localhost/api/tasks \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## Security Properties

| Property             | Mechanism                                                                         |
| -------------------- | --------------------------------------------------------------------------------- |
| Transport security   | TLS 1.2/1.3 (ECDHE ciphers only), HTTP → 301 redirect                             |
| HSTS                 | `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`         |
| Security headers     | CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy |
| Authentication       | RS256 JWT validated against Keycloak JWKS on every request                        |
| Authorization        | OPA Rego: role + path + method + device score checked before every API call       |
| Rate limiting        | Login endpoint: max 10 attempts per 60 s per IP → HTTP 429                        |
| Fail-closed          | `failure_mode_allow: false` — auth service down = all traffic denied              |
| Zero Trust perimeter | OPA, auth-middleware, testapp ports not exposed outside Docker network            |
| Read-only DB access  | SPI issues `SELECT` only — never writes to app's MySQL                            |
| Token translation    | RS256 → HS256 translation lets app work with its own existing JWT secret          |
| JWKS caching         | 5-minute in-memory cache — JWKS endpoint never becomes a bottleneck               |
| Session visibility   | Every login creates a real Keycloak session — visible in Admin Console            |
| Startup validation   | auth-middleware refuses to start if required secrets are missing                  |
