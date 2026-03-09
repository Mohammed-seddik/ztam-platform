# ZTAM Onboarding Playbook

This is the operator-facing flow for a real customer integration.

Before onboarding, read `CLIENT_INTEGRATION_PATTERNS.md` and classify the customer into a concrete integration pattern. That avoids choosing the wrong login mode under time pressure.

## 1. Discovery Call

Before touching the platform, collect these five items from the customer:

- Backend URL: where the app currently runs.
- Public hostname: what hostname you will protect, for example `app.customer.com`.
- Login mode: `form` if the app already has a login page, `keycloak` if ZTAM should host the login experience.
- Roles: the business roles they actually use, normalized to `admin`, `editor`, `user`, `viewer`.
- Test accounts: at least one admin user and one limited user for smoke testing.

If the customer wants to reuse their existing users database, collect these extra items before promising same-day onboarding:

- database engine: MySQL or PostgreSQL
- database host, port, and database name
- read-only DB username and password
- users table name
- username or email column
- password hash column
- role column if available
- password hash algorithm

Good discovery questions:

- Does the app already have its own login page?
- Who owns DNS and can create the CNAME or A record?
- Do any routes need to remain public?
- Which routes should be blocked for non-admin roles?
- Is the backend public, private-network only, or behind another reverse proxy?
- Does the customer want Keycloak to become the identity authority, or do they only want ZTAM in front of the app?
- If they want existing credentials reused, can they provide read-only DB access and describe the hash format?

### Fast decision rule

- Existing app login form and customer wants familiar UX: start with `form`
- No clean app login or you want the cleanest platform story: start with `keycloak`
- Existing users DB must remain the source of credentials: evaluate DB federation before onboarding
- Customer only wants TLS or proxy features and not centralized auth: treat that as a reduced-scope exception, not the default ZTAM model

## 2. Responsibility Split

Your side:

- Run tenant onboarding.
- Define the first permission model.
- Validate login, RBAC, and header forwarding.
- Deliver the hostname and handoff notes.

Customer side:

- Keep the backend reachable.
- Create the DNS record.
- Provide real role names and test users.
- Confirm the protected routes behave as expected.

## 3. Operator Steps

### Step A0: Assess the client website first

Before creating the tenant, probe the customer site and generate a first-pass recommendation:

```bash
python3 scripts/tenant_manager.py assess \
  --backend-url https://app.acmecorp.com \
  --name acmecorp \
  --hostname acmecorp.yourdomain.com \
  --roles "admin,editor,user,viewer" \
  --write-config
```

This does three useful things for a live integration discussion:

- checks that the backend is reachable
- detects obvious login/session behavior and recommends `form` or `keycloak`
- writes `tenants/<name>/config.json` so you start from generated configuration instead of a blank file

If the verdict is `needs-review`, you can still onboard the tenant, but you should expect client-side integration work such as redirect, cookie, or trusted-header adaptation.

If the client also wants existing DB-backed users to log in through ZTAM, stop here and confirm the database contract before promising full authentication integration.

### Step A: Create the tenant

If you already used `assess --write-config`, this step handles Keycloak registration and routing generation.

```bash
./scripts/onboard-tenant.sh \
  --name acmecorp \
  --backend https://app.acmecorp.com \
  --hostname acmecorp.yourdomain.com \
  --roles "admin,editor,user,viewer"
```

### Step B: Review the generated tenant definition

Edit `tenants/acmecorp/config.json` and make sure these fields are correct:

- `backend_url`
- `hostname`
- `login_mode`
- `roles`
- `permissions`
- `notes` if the assessment found redirect or session risks

Also explicitly confirm:

- whether this tenant should use DB federation or `--no-spi`
- whether the backend will trust forwarded identity headers only, or still expects an app-specific token shape
- whether any routes must remain public

### Step C: Regenerate policy and routing

```bash
python3 scripts/tenant_manager.py sync-policies
python3 scripts/tenant_manager.py sync-envoy
docker compose restart envoy
```

### Step D: Validate the model

```bash
python3 scripts/tenant_manager.py validate
python3 scripts/tenant_manager.py list
```

### Step E: Run the acceptance smoke test

Form-login tenant:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://acmecorp.yourdomain.com \
  --protected-path /admin \
  --username admin \
  --password admin123 \
  --expect-text "Admin Dashboard" \
  --insecure
```

Hosted-Keycloak tenant:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://legacy.yourdomain.com \
  --protected-path / \
  --login-mode keycloak \
  --insecure
```

Pre-DNS validation example:

```bash
python3 scripts/smoke_test_tenant.py \
  --base-url https://localhost \
  --host-header legacy.yourdomain.com \
  --protected-path / \
  --login-mode keycloak \
  --insecure
```

### Step F: Run the deployment audit

```bash
python3 scripts/validate_deployment.py --env-file .env --cert-dir envoy/certs --production
```

### Step G: Complete the go-live checklist

Walk through `GO_LIVE_CHECKLIST.md` and do not hand the tenant to the customer until every relevant item is closed.

For production-bound tenant or permission changes, also follow `TENANT_CHANGE_POLICY.md` so the validation evidence, approval path, and rollback notes are captured.

## 4. Customer Steps

After onboarding, the customer usually only needs to do three things:

1. Point their hostname to the ZTAM gateway.
2. Confirm the login page or redirect behavior is correct.
3. Test one admin account and one restricted account.

If DB federation is in scope, add one more customer requirement:

4. Provide and validate a read-only DB account plus at least one test user whose stored password hash is already known to be compatible.

## 5. Acceptance Test

Every integration should pass these checks before go-live:

1. Unauthenticated browser request goes to the expected login experience.
2. Valid login reaches the application.
3. Admin role reaches admin routes.
4. Restricted role is denied from protected routes.
5. Backend receives trusted headers:
   `x-user-id`, `x-user-roles`, `x-tenant-id`.
6. Spoofed identity headers from the client are ignored.

## 6. Handoff Package

At the end of the integration, hand the customer this information:

- Protected hostname
- Agreed role list
- Any custom route restrictions
- Test results summary
- Support/change process for future permission updates
- Whether identity is Keycloak-managed or federated from the customer DB
- Any known integration caveats such as redirects, cookies, or frontend assumptions

You can fill this out in `CUSTOMER_HANDOFF_TEMPLATE.md`.
Use `GO_LIVE_CHECKLIST.md` as the final operator release gate before sending the handoff.

## 7. Change Requests Later

When the customer wants a new role or new path restriction later, the workflow is:

1. Edit `tenants/<name>/config.json`
2. Run `python3 scripts/tenant_manager.py sync-policies`
3. If backend routing changed, run `python3 scripts/tenant_manager.py sync-envoy`
4. Re-test the affected role
5. Capture review, validation, and rollback details using `TENANT_CHANGE_POLICY.md`

This keeps the operational model simple: tenant config is the source of truth, generated files are outputs, and every change is reproducible.
