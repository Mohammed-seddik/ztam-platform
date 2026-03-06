# Changelog

All notable changes to the ZTAM Platform are documented in this file.

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
