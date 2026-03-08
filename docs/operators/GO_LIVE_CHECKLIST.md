# ZTAM Go-Live Checklist

Use this checklist after tenant onboarding is complete and before handing the protected hostname to a real customer.

## 1. Public Identity

- [ ] `KC_HOSTNAME` is set to the real public Keycloak hostname.
- [ ] `KEYCLOAK_URL` is set for the environment auth-middleware will actually call.
- [ ] `KC_ISSUER_URL` matches the issuer stamped into production tokens.
- [ ] `ZTAM_PUBLIC_URL` is the real public ZTAM gateway URL.
- [ ] No production-facing URL still points to `localhost` or a placeholder domain.

## 2. Secrets

- [ ] `PG_PASS` is not a default value.
- [ ] `KC_ADMIN_PASS` is not a default value.
- [ ] `KC_CLIENT_SECRET` is not a default value.
- [ ] `TESTAPP_JWT_SECRET` is at least 32 characters.
- [ ] Cookie settings are production-safe: `AUTH_COOKIE_SECURE=true` and an intentional `AUTH_COOKIE_SAMESITE` value.

## 3. TLS And DNS

- [ ] The tenant hostname points to the ZTAM gateway.
- [ ] `envoy/certs/server.crt` exists and is the intended certificate.
- [ ] `envoy/certs/server.key` exists and matches the certificate.
- [ ] The certificate covers the hostname being handed to the customer.
- [ ] HTTP redirects cleanly to HTTPS.

## 4. Tenant Definition

- [ ] `tenants/<name>/config.json` matches the intended backend URL, hostname, login mode, roles, and permissions.
- [ ] `python3 scripts/tenant_manager.py validate` passes.
- [ ] `python3 scripts/tenant_manager.py sync-policies` has been run after the final permission edit.
- [ ] `python3 scripts/tenant_manager.py sync-envoy` has been run after the final routing edit.
- [ ] Envoy has been restarted or reloaded after the final routing change.

## 5. Runtime Validation

- [ ] `python3 scripts/validate_deployment.py --env-file .env --cert-dir envoy/certs --production` passes.
- [ ] Unauthenticated browser traffic lands on the expected login experience.
- [ ] Spoofed user headers do not grant access.
- [ ] An admin account can reach the intended protected route.
- [ ] A restricted account is denied from a route it should not reach.

Form-login example:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://app.yourdomain.com \
  --protected-path /admin \
  --username admin \
  --password admin123 \
  --expect-text "Admin Dashboard"
```

Hosted-Keycloak example:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://legacy.yourdomain.com \
  --protected-path / \
  --login-mode keycloak
```

Pre-DNS hosted-Keycloak example:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://localhost \
  --host-header legacy.yourdomain.com \
  --protected-path / \
  --login-mode keycloak \
  --insecure
```

## 6. Customer Handoff

- [ ] `CUSTOMER_HANDOFF_TEMPLATE.md` is filled with the real tenant details.
- [ ] The customer has the protected hostname and expected login mode.
- [ ] The customer has the agreed role list and any custom restrictions.
- [ ] The customer knows the support/change-request process.
- [ ] The final smoke-test date and result are recorded.

## 7. Final Decision

- [ ] Go live now.
- [ ] Block release until any unchecked item is closed.
