# Customer Handoff Template

Use this after a tenant integration is completed.

## Customer

- Customer name:
- Tenant name:
- Protected hostname:
- Backend URL:
- Login mode: `form` or `keycloak`
- Identity source: `keycloak-managed`, `client-db-federated`, or `customer-owned-auth exception`
- Environment: `demo`, `staging`, or `production`

## Roles Agreed

- Admin roles:
- Standard roles:
- Restricted roles:

## What Was Integrated

- ZTAM is now the public entry point for the application.
- TLS termination is enforced at the gateway.
- Authentication is handled by Keycloak.
- Authorization is enforced centrally by OPA.
- Trusted identity headers are forwarded to the backend.

## Integration Pattern

- Client type: existing login page, hosted-keycloak app, SPA/API, legacy internal app, or DB-federated app
- Why this login mode was chosen:
- Any required app-side adaptation:

## Identity And Database Notes

- Identity authority:
- Existing user database reused: yes or no
- If yes, DB engine:
- If yes, login column:
- If yes, password hash column:
- If yes, role column:
- If yes, hash algorithm:
- If yes, DB access model: read-only account confirmed yes or no

## Validation Summary

- Unauthenticated access behavior:
- Admin login test:
- Restricted-role authorization test:
- Spoofed-header test:
- Final smoke test date:
- Production audit result:
- Go-live checklist status:

## Routing And Policy Summary

- Public routes, if any:
- Protected routes tested:
- Admin-only routes:
- Restricted-role denial paths:
- Final agreed role set:

## Customer Actions

1. Keep the backend reachable from the ZTAM gateway.
2. Maintain the DNS record for the protected hostname.
3. Provide notice before changing app routes or login behavior.
4. If DB federation is used, notify the platform team before changing schema, password hashing, or auth-related columns.

## Future Change Process

1. Update `tenants/<name>/config.json`
2. Run `python3 scripts/tenant_manager.py sync-policies`
3. If routing changed, run `python3 scripts/tenant_manager.py sync-envoy`
4. Re-run the tenant smoke test

## Support Notes

- Escalation contact:
- Repo or environment:
- Special restrictions or exceptions:
- Known risks or follow-up tasks:
- Final operator sign-off:
