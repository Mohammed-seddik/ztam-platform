# ZTAM Platform — Zero Trust Application Middleware

> Protects existing web applications behind Keycloak authentication, OPA policy enforcement,
> and gateway-managed identity translation with minimal application changes.

```
Browser ─── HTTPS :443 ──▶ Envoy (TLS termination)
                │                │
                │                ├──▶ Auth Middleware  (internal only)
                │                │         │
                │                │         ├──▶ Keycloak JWKS (RS256 validation)
                │                │         ├──▶ OPA (policy decision)
                │                │         └──▶ Keycloak token endpoint (login)
                │
                └──▶ TestApp :3000  (bundled demo app, internal only)

HTTP :80 → 301 redirect → HTTPS :443
```

---

## What's Inside

| Container         | Image                      | Exposed Port      | Role                                               |
| ----------------- | -------------------------- | ----------------- | -------------------------------------------------- |
| `envoy`           | envoyproxy/envoy:v1.28.7   | **80, 443**       | TLS termination + reverse proxy + ext_authz        |
| `keycloak`        | keycloak:26.5.5            | 8080 (admin only) | Identity provider + session management             |
| `auth-middleware` | FastAPI / Python 3.12      | — (internal)      | JWT validation + rate limiting + token translation |
| `control-plane`   | FastAPI / Python 3.12      | 8010 (localhost)  | Tenant lifecycle, validation, publishing, audit    |
| `opa`             | openpolicyagent/opa:0.64.1 | — (internal)      | Policy engine (Rego rules)                         |
| `testapp`         | Node.js 20                 | — (internal)      | Bundled sample protected app                       |
| `testapp-db`      | mysql:8                    | — (internal)      | Bundled sample app's user + task database          |
| `postgres`        | postgres:16                | — (internal)      | Keycloak's internal storage                        |

> All internal services are **not reachable from outside Docker** — only Envoy (443/80) and Keycloak admin (8080) are exposed. This enforces the Zero Trust perimeter.

---

## Prerequisites

- Docker + Docker Compose v2
- Maven 3.9+ with JDK 17+ — (Optional) needed only if you use the Keycloak MySQL SPI
- Python 3.9+ (on the host) — for onboarding scripts
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

> **Production mode:** The `keycloak` service runs with `command: start` (production mode, not `start-dev`).
> This requires `KC_HOSTNAME`, a backing Postgres database, and an HTTP-enabled listener — all of which
> are already configured in `docker-compose.yml`. For a publicly reachable deployment, also set
> `KC_ISSUER_URL` and `ZTAM_PUBLIC_URL` in `.env` to the real FQDN, and restrict port 8080 behind a firewall.

Wait ~30 s for Keycloak to finish initializing, then:

### 5 — Prime Keycloak (one-time)

Automatically creates the realm, Keycloak client, MySQL SPI registration,
and JWT protocol mappers, then smoke-tests the full login flow:

```bash
python3 demo/setup_demo.py
```

To force TOTP MFA enrollment on first login:

```bash
python3 demo/setup_demo.py --enable-mfa
```

The root-level `setup_demo.py` wrapper still works for backward compatibility.

### 6 — Open the app

```
https://localhost
```

HTTP requests to `http://localhost` are automatically redirected to HTTPS.

### 7 — Bootstrap the control plane (optional but recommended)

Import the legacy demo tenant into the new control-plane database:

```bash
python3 scripts/bootstrap_control_plane.py
```

Control-plane API:

```text
http://127.0.0.1:8010
```

---

## Test Users

All users live in the bundled demo app's MySQL DB. Keycloak reads and authenticates them via the SPI.

| Username   | Password    | Role      | What they see            |
| ---------- | ----------- | --------- | ------------------------ |
| `alice`    | `secret123` | **admin** | All tasks from all users |
| `charlie`  | `pass123`   | **user**  | Only their own tasks     |
| `testuser` | `test123`   | **user**  | Only their own tasks     |
| `demouser` | `demo123`   | **admin** | All tasks from all users |

---

## Multi-Client Onboarding

ZTAM is designed for multi-tenancy. You can onboard any number of client applications in seconds.

Core docs by audience:

- Demo and review: `docs/demo/DEMO_PRESENTATION_GUIDE.md`, `docs/architecture/PROJECT_EXPLANATION.md`, `docs/architecture/ARCHITECTURE.md`
- Operators: `docs/operators/ONBOARDING_PLAYBOOK.md`, `docs/operators/CLIENT_INTEGRATION_PATTERNS.md`, `docs/operators/DEPLOYMENT.md`, `docs/operators/GO_LIVE_CHECKLIST.md`
- Governance: `docs/operators/INTEGRATION_CONTRACT.md`, `docs/operators/TENANT_CHANGE_POLICY.md`, `docs/operators/OBSERVABILITY_RUNBOOK.md`
- Delivery and roadmap: `docs/operators/CUSTOMER_HANDOFF_TEMPLATE.md`, `docs/product/EXECUTION_PLAN.md`, `docs/product/ENTERPRISE_ROADMAP.md`

### Tomorrow's first-client path

If you are onboarding a real client, do not start by hand-editing configs.

Use this order:

1. Read `docs/operators/CLIENT_INTEGRATION_PATTERNS.md` to classify the client type.
2. Run `python3 scripts/tenant_manager.py assess --backend-url <client-url> --name <tenant> --hostname <host> --roles "admin,manager,user" --write-config`.
3. Confirm the recommended login mode, role list, and any redirect or cookie risks.
4. Follow `docs/operators/ONBOARDING_PLAYBOOK.md` for tenant creation, validation, smoke test, and handoff.
5. Do not release until `docs/operators/GO_LIVE_CHECKLIST.md` is closed.

### Optional observability profile

To run the local monitoring stack:

```bash
docker compose --profile observability up -d prometheus grafana
```

Endpoints:

- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3001`

Default Grafana login:

- username: `admin`
- password: `change_me_grafana` unless overridden in `.env`

Prometheus scrapes the auth-middleware `/metrics` endpoint on the internal Docker network.
Grafana auto-provisions the Prometheus datasource and a starter dashboard named `ZTAM Overview`.

The repo is in migration to a control-plane source-of-truth model:

- control-plane DB/API is the target source of truth
- `platform/published/` contains generated runtime bundles
- `tenants/<name>/config.json` remains as a legacy import/export path during migration

Published runtime bundles used by the data plane:

- `platform/published/auth/tenants.json`
- `platform/published/policy/tenants.json`
- `platform/published/routing/tenants.json`

Legacy tenant config still exists for backward compatibility:

- `tenants/<name>/config.json` stores backend metadata, login mode, roles, and tenant-specific permissions
- `python3 scripts/tenant_manager.py validate` checks tenant definitions before apply
- `python3 scripts/tenant_manager.py sync-policies` regenerates `policies/tenants.json` from tenant configs
- `python3 scripts/tenant_manager.py sync-envoy` regenerates tenant routes/clusters in `envoy/envoy.yaml`

### Case A: App with its own Login Page

Uses the **Form Login Mode** only when preserving the application's login UX is a hard requirement. This is not the default platform experience.

```bash
./scripts/onboard-tenant.sh \
  --name myapp \
  --backend https://myapp.internal \
  --hostname myapp.yourdomain.com
```

### Case B: App with NO Login Page

Uses the **Keycloak Login Mode**. This is the default and preferred v1 experience. Unauthenticated users are redirected to Keycloak's own login UI.

```bash
./scripts/onboard-tenant.sh \
  --name legacy-app \
  --backend http://10.0.0.5:8080 \
  --hostname legacy.yourdomain.com \
  --login-mode keycloak \
  --no-spi
```

### Case C: Client Wants Existing Users From Their Own Database

If the client wants ZTAM or Keycloak to authenticate against their existing MySQL or PostgreSQL user database, treat that as a federation decision, not just a routing decision.

Collect these inputs first:

- DB engine, host, port, and database name
- a dedicated read-only DB username and password
- users table name
- username or email column
- password hash column
- role column if available
- hash algorithm, preferably bcrypt

Use `docs/operators/CLIENT_INTEGRATION_PATTERNS.md` to decide whether DB federation is actually required for that tenant.

### Offboarding a Tenant

```bash
./scripts/offboard-tenant.sh --name myapp
```

### Validate the current onboarding model

```bash
python3 scripts/tenant_manager.py validate
python3 scripts/tenant_manager.py list
python3 scripts/tenant_manager.py assess --backend-url https://app.customer.com
python3 scripts/smoke_test_tenant.py --base-url https://localhost --protected-path /api/tasks --login-mode keycloak --insecure
python3 scripts/smoke_test_tenant.py --base-url https://localhost --host-header newtenant.yourdomain.com --protected-path / --login-mode keycloak --insecure
python3 scripts/validate_deployment.py --env-file .env --cert-dir envoy/certs
cat docs/operators/GO_LIVE_CHECKLIST.md
```

Teacher/demo shortcut:

```bash
./scripts/demo_teacher_flow.sh
```

To generate a starter tenant config dynamically from a client URL:

```bash
python3 scripts/tenant_manager.py assess \
  --backend-url https://app.customer.com \
  --name customerapp \
  --hostname customerapp.yourdomain.com \
  --roles "admin,manager,user" \
  --write-config
```

---

## Production Deployment

For full production setup instructions (TLS via Let's Encrypt, DNS, Hardening), see [DEPLOYMENT.md](docs/operators/DEPLOYMENT.md).

## Exposed Ports

| URL                     | What                           |
| ----------------------- | ------------------------------ |
| `https://localhost`     | ✅ Main entry point — use this |
| `http://localhost`      | Redirects → HTTPS (301)        |
| `http://localhost:8080` | Keycloak Admin Console         |

**Keycloak admin login:** `admin` / `<KC_ADMIN_PASS from .env>`

OPA, auth-middleware, and testapp are **not** exposed externally — Zero Trust perimeter.
The optional observability profile exposes Prometheus and Grafana on loopback only for local review.

---

## Login Flow (step by step)

1. Browser opens `https://localhost`
2. Envoy pauses the protected request and auth-middleware sees there is no token
3. auth-middleware redirects the browser to the Keycloak login page
4. User signs in on Keycloak
5. Keycloak authenticates the user, including the SPI-backed demo user path
6. Keycloak redirects back to ZTAM callback with an authorization code
7. auth-middleware exchanges the code for a Keycloak RS256 access token
8. auth-middleware stores the token in a secure cookie and redirects back to the app

For every subsequent API call:

9. Browser sends the secure auth cookie on later requests
10. Envoy's `ext_authz` filter pauses the request and forwards it to auth-middleware
11. auth-middleware validates the Keycloak RS256 token against JWKS
12. auth-middleware calls OPA with the v1 auth context `{ tenant, subject, request, client, device }`
13. On allow: auth-middleware injects trusted identity headers for the backend
14. If the tenant uses adapter mode `translated_token`, auth-middleware also mints a downstream token for that specific app
15. Envoy forwards the request to the backend with trusted identity context

See [ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) for full diagrams.

---

## OPA Policy Summary

Rules are in `policies/authz.rego`. Global fallback permissions are in `policies/permissions.json`, and published tenant policy data is generated into `platform/published/policy/tenants.json`.

| Role       | Methods                       | Paths                              |
| ---------- | ----------------------------- | ---------------------------------- |
| **admin**  | All                           | All                                |
| **editor** | GET, POST, PUT, PATCH, DELETE | `/api/*` — blocked from `/admin/*` |
| **user**   | GET, POST                     | `/api/*` — blocked from `/admin/*` |
| **viewer** | GET                           | `/api/*` — blocked from `/admin/*` |

**Device trust:** the auth contract now includes `device`, but real posture enforcement is still deferred. The default posture is `unknown`.

---

## Directory Structure

```
ztam-platform/
├── docs/                       # Grouped docs by audience
│   ├── architecture/
│   ├── demo/
│   ├── operators/
│   └── product/
├── envoy/
│   ├── envoy.yaml              # Routing, TLS, ext_authz, security headers
│   ├── generate-certs.sh       # Self-signed TLS cert generator
│   └── certs/                  # server.crt + server.key (not in git)
├── platform/
│   ├── control-plane/          # Tenant admin API + publish workflow
│   ├── contracts/              # Canonical v1 schemas
│   └── published/              # Generated runtime bundles
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
├── demo/
│   ├── testapp/                # Bundled sample protected app
│   ├── setup_demo.py           # Demo Keycloak bootstrap + smoke test
│   ├── demo_test.sh            # Demo helper script
│   └── README.md               # Demo-vs-platform boundary
├── docker-compose.yml
├── .env                        # Secrets — never committed
├── .env.example
├── setup_demo.py               # Compatibility wrapper → demo/setup_demo.py
└── demo_test.sh                # Compatibility wrapper → demo/demo_test.sh
```

Repo layout by purpose:

- Root: runtime entrypoints and deployment files.
- `docs/`: grouped documentation by audience instead of many mixed top-level markdown files.
- `platform/`: the new control-plane and generated runtime-bundle architecture.
- `demo/`: bundled sample app and demo-only helpers.
- `services/`, `envoy/`, `policies/`, `scripts/`, `tenants/`: the real platform runtime and operator workflow.
- `keycloak-db-spi/`: isolated Java extension project for the demo identity bridge.

Important root docs:

- `README.md`: start here for setup and operator shortcuts
- `docs/architecture/ARCHITECTURE.md`: request flow and component model
- `docs/demo/DEMO_PRESENTATION_GUIDE.md`: tomorrow-ready presentation script
- `docs/architecture/PROJECT_EXPLANATION.md`: full project narrative in plain language
- `docs/operators/INTEGRATION_CONTRACT.md`: realistic client integration expectations

If you use this repo in VS Code, the workspace hides `.venv`, `__pycache__`, `target`, and `build-verify` so the explorer stays focused on source files.

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
