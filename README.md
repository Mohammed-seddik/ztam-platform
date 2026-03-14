# Zero Trust MVP (Legacy Adapter Demo)

This repository now contains a **single-machine MVP** demonstrating:

`User -> Envoy Gateway -> Keycloak -> client app user verification -> app access`

This is intentionally simple and optimized for a same-day demo.

## Folder structure

```text
.
├── docker-compose.yml
├── certs/
├── envoy/
│   ├── envoy.yaml
│   └── generate-certs.sh
├── keycloak/
│   ├── realm-demo-client.json
│   └── setup-realm.sh
├── keycloak-spi/
│   ├── Dockerfile
│   ├── pom.xml
│   └── src/main/
│       ├── java/com/ztam/keycloak/
│       │   ├── RestAuthenticator.java
│       │   ├── RestAuthenticatorFactory.java
│       │   └── VerifyResponse.java
│       └── resources/META-INF/services/
│           └── org.keycloak.authentication.AuthenticatorFactory
├── client-app/
│   ├── Dockerfile
│   ├── app.py
│   ├── requirements.txt
│   └── templates/
│       ├── home.html
│       └── login.html
└── mysql/
    └── init/
        └── 001-schema-seed.sql
```

## What is included

- **Envoy** on HTTPS `https://localhost` (with optional HTTP->HTTPS redirect).
  See [docs/architecture/PROXY.md](docs/architecture/PROXY.md) for a detailed explanation
  of how the proxy is configured and used.
- **Keycloak 26.0.7** with Postgres.
- **Custom Keycloak Authenticator SPI** that calls `POST /auth/verify`.
- **Client app (Flask)** with protected home page and MySQL-backed users/roles.
- **MySQL init** with seeded users:
  - `alice / password123 / admin`
  - `bob / password123 / user`

## Security controls in this MVP

- Envoy strips incoming identity headers before forwarding.
- Envoy admin API bound to `127.0.0.1` only.
- `/auth/verify` requires `Authorization: Bearer <shared_api_key>`.
- Passwords are hashed (SHA-256 + per-user salt for demo simplicity).
- Secrets are env-configurable in `docker-compose.yml`.
- Keycloak port is bound to localhost only: `127.0.0.1:8080:8080`.

## Build the SPI JAR (optional local build)

The compose setup builds the SPI automatically via `keycloak-spi/Dockerfile`.

If you want to build manually:

```bash
cd keycloak-spi
mvn -DskipTests package
```

Generated jar:

```text
keycloak-spi/target/keycloak-rest-authenticator-1.0.0.jar
```

## Run the stack

1. Generate local TLS certs for Envoy:

```bash
./envoy/generate-certs.sh
```

2. Start everything:

```bash
docker compose up --build
```

3. Configure Keycloak custom browser flow (one-time after Keycloak is healthy):

```bash
docker compose exec keycloak /opt/keycloak/keycloak/setup-realm.sh
```

> If script path is unavailable in container, copy+run manually:
>
> ```bash
> docker cp keycloak/setup-realm.sh $(docker compose ps -q keycloak):/tmp/setup-realm.sh
> docker compose exec keycloak bash /tmp/setup-realm.sh
> ```

## Test the login flow

### 1) Envoy HTTPS responds

```bash
curl -kI https://localhost
```

### 2) Keycloak login page reachable

```bash
curl -I http://localhost:8080/realms/demo-client/account
```

### 3) Verify endpoint tests

Correct API key + correct credentials:

```bash
curl -s http://localhost:3000/auth/verify \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer demo-shared-api-key' \
  -d '{"username":"alice","password":"password123"}'
```

Wrong password:

```bash
curl -s http://localhost:3000/auth/verify \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer demo-shared-api-key' \
  -d '{"username":"alice","password":"wrong"}'
```

Wrong API key (expect HTTP 401):

```bash
curl -i http://localhost:3000/auth/verify \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer wrong-key' \
  -d '{"username":"alice","password":"password123"}'
```

### 4) Browser login through Keycloak

1. Open `https://localhost`
2. Click login
3. Use `alice / password123`
4. Confirm protected page loads and shows `db_user_id` + role.

Wrong password should show Keycloak login error.

### 5) Verify `/auth/verify` logs

```bash
docker compose logs -f client-app
```

Look for log line: `/auth/verify called username=...`

## Verify app access goes through Envoy

- Access app via `https://localhost` (Envoy only).
- Client app is not published externally by compose (no host `ports` mapping).
- Envoy strips spoofed identity headers and proxies to internal `client-app:3000`.

## Known MVP limitations

- Hashing uses SHA-256+salt for speed of demo (not production-grade password policy).
- Keycloak flow setup is a post-start script.
- No refresh token/session hardening.
- No tenant isolation.
- No centralized policy engine.

## How to evolve this MVP into the better long-term architecture

1. **OIDC/SAML federation first**
   - Integrate enterprise IdPs as primary auth path.
   - Use Keycloak broker/federation to avoid password handling where clients already have SSO.

2. **User storage/federation second**
   - Add user federation or SCIM sync for profile lifecycle.
   - Gradually reduce reliance on app-local credentials.

3. **Custom REST SPI only as fallback**
   - Keep this legacy `/auth/verify` adapter for customers without federation.
   - Treat it as transition mode, not the default architecture.
