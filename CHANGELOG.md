# Changelog

All notable changes to the ZTAM Platform are documented in this file.

---

## [v4.1.0] — Production Hardening & Security Fixes

### Fixed (Security)

- **Critical:** Removed admin self-selection from `register.html` — users can no longer register
  as `admin` via the registration form or request body. Role is now always `"user"` server-side.
- **CORS wildcard removed** in `testapp/backend/server.js` — replaced `cors()` (allow-all) with
  same-origin policy (`origin: false`). All legitimate browser traffic arrives via Envoy on the
  same host, so no cross-origin access is needed.
- **JSON body size limit** added to Express (`100kb`) — prevents oversized-JSON DoS attacks.

### Fixed (Functional)

- **`setup_demo.py` completely rewritten** — now fully idempotent and covers the entire Keycloak
  initialisation sequence:
  1. Reads credentials from `.env` (no more manual `export` required).
  2. Creates the `test-tenant` realm if absent.
  3. Creates the `test-app` confidential client if absent, and synchronises the client secret.
  4. Registers the MySQL SPI user-federation component if absent.
  5. Creates `role` and `db_user_id` protocol mappers if absent.
  6. Deletes any native Keycloak users (forces SPI federation).
  7. Smoke-tests login as `alice` and prints token claims.
     Previously the script only deleted users and tested login — the realm/client/SPI/mapper
     steps were entirely missing, meaning the platform could not be initialised from scratch.
- **`KC_ADMIN_PASS` / `KC_ADMIN_PASSWORD` inconsistency fixed** in `onboard-tenant.sh`:
  the script now accepts `KC_ADMIN_PASS` (as documented in `.env.example`) or the legacy
  `KC_ADMIN_PASSWORD` alias. Previously the script would exit immediately on any fresh `.env`.

### Added

- **Server-side input validation** on `POST /api/auth/register`:
  - Username: 3–50 characters (enforced server-side and on the form).
  - Password: 8–200 characters (minimum raised from 6 to 8, synced front-end/back-end).
  - Role is never accepted from the request body.
- **Rate limiting on `POST /api/auth/register`** — max 10 attempts per IP per 60 s (HTTP 429).
  Previously only the login endpoint was rate-limited.
- **Task title server-side length limit** — max 200 characters; returns HTTP 400 if exceeded.
- **Proactive JWT expiry check** in `dashboard.html` — decodes `exp` claim on page load and
  redirects to login immediately if the token is already expired.
- **Centralised auth-failure handler** in `dashboard.html` — all API calls (load, add, delete)
  redirect to `/login.html` on HTTP 401 or 403, covering token expiry mid-session.

### Changed

- `.env.example` — added `KEYCLOAK_URL` entry, added `openssl rand` generation instructions,
  and clearer comment block.
- `README.md` — added production warning about `start-dev` Keycloak mode
  and clarified what `setup_demo.py` does.
- `ARCHITECTURE.md` — corrected Keycloak version to `26.5.5` (was `26.3`).
- **Removed redundant/conflicting OPA data files**: `policies/data/permissions.json` and
  `policies/permissions/permissions.json`. Both were loaded by OPA under wrong namespaces
  (`data.data.permissions` and `data.permissions.permissions`) — neither was used by any
  Rego rule but the `policies/permissions/` sub-path conflicted with `policies/permissions.json`.

### Infrastructure

- **Java 21 LTS upgrade** for `keycloak-db-spi` module:
  - Migrated from Java 17 to Java 21 (LTS) with full bytecode verification (major version 65).
  - Updated Maven compiler plugin to 3.13.0 and all compiler configurations.
  - All dependencies verified as Java 21 compatible (Keycloak 26.5.5, MySQL Connector/J 8.3.0).
  - CVE scan: 0 vulnerabilities detected across all dependencies.
  - Build artifacts now target Java 21 runtime environments.
  - Upgrade session: `20260306161630` — detailed logs in `.github/java-upgrade/`.

---

## [v4.0.0] — Multi-Tenant Dynamic Onboarding

### Added

- **`scripts/onboard-tenant.sh`** — one command onboards any external app (Railway, Heroku, Render, VPS, Docker): creates Keycloak client + roles, adds OPA permissions, configures Envoy routing, reloads automatically
- **`policies/tenants.json`** — central per-tenant permissions file, hot-reloaded by OPA (< 1s, no restart)
- **`scripts/opa_add_tenant.py`** — adds a tenant entry to `tenants.json` with sensible role defaults
- **`scripts/envoy_add_tenant.py`** — inserts Envoy virtual host + cluster using sentinel markers. Idempotent
- **`tenants/`** directory — per-tenant `config.json` metadata + `_template/` for manual onboarding
- **`INTEGRATION_GUIDE.md`** — sales pitch, 6 integration cases, live demo playbook, pricing tiers

### Changed

- `policies/authz.rego` — tenant-aware `_resolve_perms()`: checks `tenants.json[tenant_id]` first, falls back to global `permissions.json`. Uses `object.get()` for safe missing-key handling
- `policies/authz_test.rego` — 14 → 16 tests (added 2 multi-tenant tests); all pass
- `envoy/envoy.yaml` — sentinel markers (`__ZTAM_TENANT_ROUTES__`, `__ZTAM_TENANT_CLUSTERS__`) for automated multi-tenant insertion

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v3.0.0] — Production Security Hardening

### Added

- **TLS termination** on Envoy: HTTPS on port 443 (TLS 1.2/1.3), HTTP→HTTPS redirect on port 80
- **Security response headers** on all HTTPS responses: `Strict-Transport-Security`, `Content-Security-Policy`, `Permissions-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`
- **Per-IP rate limiting** in auth-middleware: 10 requests / 60-second window → HTTP 429
- **Startup validation** in auth-middleware: fails fast if required environment variables are missing
- **Input length guard** in auth-middleware: rejects tokens exceeding 8 KB
- **Log scrubbing** in auth-middleware: token values never appear in log output
- `KC_ISSUER_URL` environment variable to decouple Keycloak public URL from internal service hostname
- `KC_ADMIN_USER` documented in `.env.example`

### Fixed

- **JWT audience validation failure**: `aud` claim in Keycloak access tokens is `"account"`, not the client ID — replaced `audience=KC_CLIENT_ID` with a manual `azp` claim check
- **JWT issuer mismatch**: `KC_HOSTNAME: localhost` causes Keycloak to issue tokens with `iss: http://localhost:8080/...`; `EXPECTED_ISSUER` now derives from `KC_ISSUER_URL` instead of the internal `keycloak:8080` hostname
- Removed exposed ports for OPA, auth-middleware, and testapp (zero-trust: only Envoy is reachable externally)
- Removed credential fallback defaults from `docker-compose.yml`
- Corrected DELETE / PUT / PATCH permission entries for `user` and `editor` roles in `permissions.json`

### Changed

- `README.md` and `ARCHITECTURE.md` fully updated: HTTPS curl examples, TLS setup, rate limiting description, updated security-properties table, corrected OPA permissions table, updated directory structure

---

## [v2.0.0] — Security Hardening & Structural Fixes

### Added

- Full testapp source code added to repository (replaced broken git submodule reference)
- OPA debug image (`openpolicyagent/opa:0.64.1-debug`) for improved policy development
- Production hardening across services: env-variable-driven configuration, no hardcoded secrets

### Fixed

- Orphaned testapp gitlink replaced with actual source files
- Removed unused TestApp submodule reference from `.gitmodules`

### Changed

- Consolidated security hardening across auth-middleware, Envoy, and OPA services

---

## [v1.0.0] — Initial Release

### Added

- **ZTAM Platform** — Zero Trust Access Management proof-of-concept
- **Envoy proxy** as the single entry point with `ext_authz` filter delegating all authorization decisions to auth-middleware
- **Keycloak 26.3.0** as the Identity Provider (realm `test-tenant`, client `test-app`, roles: `admin`, `editor`, `user`)
- **auth-middleware** (FastAPI / Python 3.12) — validates Keycloak JWT tokens and enforces OPA policy decisions
- **OPA 0.64.1** — attribute-based access control via `policies/permissions.json` and `policies/authz.rego`
- **testapp** (Node.js / Express) — sample protected backend serving a task list based on user role
- `docker-compose.yml` orchestrating all services with health checks and dependency ordering
- 14-test OPA policy test suite (`policies/authz_test.rego`) — all passing
- `.env.example` documenting all required environment variables
- `README.md` with quickstart, architecture overview, and example curl commands
- `ARCHITECTURE.md` with component diagram and data-flow description

[v3.0.0]: https://github.com/Mohammed-seddik/ztam-platform/releases/tag/v3.0.0
[v2.0.0]: https://github.com/Mohammed-seddik/ztam-platform/releases/tag/v2.0.0
[v1.0.0]: https://github.com/Mohammed-seddik/ztam-platform/releases/tag/v1.0.0
