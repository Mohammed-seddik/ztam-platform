# ZTAM Integration Guide

## Selling & Onboarding Client Apps With Minimal Changes

---

## What Is ZTAM?

ZTAM (Zero Trust Access Management) sits in front of **any web app** and handles:

| What we provide                  | What the client needs to do |
| -------------------------------- | --------------------------- |
| SSL/TLS termination (HTTPS)      | Nothing                     |
| Login / logout via Keycloak      | Nothing                     |
| JWT validation on every request  | Nothing                     |
| Role-based access control (OPA)  | Define their roles once     |
| Rate-limiting & security headers | Nothing                     |
| User identity headers to backend | Optionally read them        |

The client's app often needs little or no code change, but you should not promise zero effort before checking redirects, cookies, login assumptions, and routing behavior.

For the operator decision model, use `CLIENT_INTEGRATION_PATTERNS.md` together with `ONBOARDING_PLAYBOOK.md`.

---

## The 5-Minute Pitch

> "Your app is currently exposed directly.
> We place a security layer in front of it that centralizes authentication,
> route-level authorization, HTTPS, and trusted identity forwarding.
> In many cases the app needs little or no code change, but we validate the
> integration pattern first instead of guessing."

---

## Architecture (Multi-Tenant)

```
          Internet
             │
   ┌─────────▼─────────┐
   │      Envoy         │  TLS 1.2/1.3, security headers
   │  (port 443 / 80)   │  routes by hostname
   └─────────┬─────────┘
             │ ext_authz (every request)
   ┌─────────▼─────────┐
   │  auth-middleware   │  validates RS256 JWT (Keycloak)
   │   (FastAPI)        │  rate-limiting, input validation
   └─────────┬─────────┘
             │ OPA decision
   ┌─────────▼─────────┐
   │       OPA          │  per-tenant RBAC rules
   │  (0.64.1)          │  policies/tenants.json (hot-reload)
   └─────────────────────┘
             │ allow → forward with x-user-* headers
   ┌─────────▼──────────────────────────────────────┐
   │   Client A backend   │  Client B backend        │
   │ (Railway / Render)   │ (Docker / VPS / Heroku)  │
   └──────────────────────┴──────────────────────────┘
```

Each client gets their own:

- **Keycloak client** in the shared realm
- **Hostname / subdomain** → `clientname.yourdomain.com`
- **Tenant config** in `tenants/<name>/config.json`
- **Generated policy entry** in `policies/tenants.json`

For a real client engagement, use `ONBOARDING_PLAYBOOK.md` as the operator checklist.
Use `GO_LIVE_CHECKLIST.md` for final production sign-off.

---

## Onboarding a New Client — Step by Step

Before onboarding, classify the client using `CLIENT_INTEGRATION_PATTERNS.md`.

That prevents the two most common mistakes:

1. choosing `form` mode for an app that should really use `keycloak`
2. promising DB-backed credential reuse without confirming the database contract

### Prerequisites

- ZTAM is running (`docker compose up -d`)
- `.env` file is populated (see `.env.example`)
- The client's backend app is accessible at a URL (public or private)

### One command

```bash
./scripts/onboard-tenant.sh \
  --name     myapp                         \
  --backend  https://myapp.railway.app     \
  --hostname myapp.yourdomain.com          \
  --roles    "admin,editor,user,viewer"

# Optional flags:
#   --login-mode keycloak   # Hosted Keycloak login instead of ZTAM form login
#   --no-spi                # Skip external DB/SPI assumptions
```

That's it. The script:

1. Creates a Keycloak client `myapp` with the specified roles
2. Saves the tenant config to `tenants/myapp/config.json`
3. Regenerates `policies/tenants.json` from all tenant configs
4. Regenerates tenant routes and clusters in `envoy/envoy.yaml`
5. Reloads Envoy

### Source of truth

The important rule is this:

- `tenants/<name>/config.json` is the source of truth
- `policies/tenants.json` is generated from tenant configs
- tenant routes/clusters in `envoy/envoy.yaml` are generated from tenant configs
- onboarding validates tenant configs before they are trusted operationally

Useful commands:

```bash
python3 scripts/tenant_manager.py validate
python3 scripts/tenant_manager.py list
python3 scripts/tenant_manager.py sync-policies
python3 scripts/tenant_manager.py sync-envoy
python3 scripts/smoke_test_tenant.py --base-url https://myapp.yourdomain.com --protected-path /
python3 scripts/smoke_test_tenant.py --base-url https://localhost --host-header myapp.yourdomain.com --protected-path / --login-mode keycloak --insecure
python3 scripts/validate_deployment.py --env-file .env --cert-dir envoy/certs
```

### What the client sees

After DNS change (`myapp.yourdomain.com → your-server-ip`):

- `https://myapp.yourdomain.com/api/auth/login` → login endpoint
- `https://myapp.yourdomain.com/**` → requires JWT, enforced by ZTAM
- Their backend receives every authenticated request with these headers:
  ```
  x-user-id:    <keycloak-uuid>
  x-user-roles: admin
  x-tenant-id:  myapp
  ```

---

## Client Integration Cases

### Case 1: App on a Cloud Platform (Railway, Render, Heroku, Fly.io)

**Client**: "I have my app deployed at `https://myapp.up.railway.app`"

```bash
./scripts/onboard-tenant.sh \
  --name     myapp \
  --backend  https://myapp.up.railway.app \
  --hostname myapp.yourdomain.com \
  --roles    "admin,user"
```

**DNS**: Client adds `CNAME myapp.yourdomain.com → your-ztam-server-ip`

**No code change needed** on the client's side.

---

### Case 2: App on a VPS or Dedicated Server

**Client**: "My app runs on port 3000 of my VPS at `192.168.1.50`"

```bash
./scripts/onboard-tenant.sh \
  --name     myclient \
  --backend  http://192.168.1.50:3000 \
  --hostname myclient.yourdomain.com \
  --roles    "admin,staff,readonly"
```

**No TLS needed** between ZTAM and the client's backend (they're on private network).

---

### Case 3: App with Custom Roles

**Client**: "We have 4 roles that map cleanly to `admin`, `editor`, `user`, `viewer`"

```bash
./scripts/onboard-tenant.sh \
  --name     acmecorp \
  --backend  https://app.acmecorp.com \
  --hostname acmecorp.yourdomain.com \
  --roles    "admin,editor,user,viewer"
```

Then customize `tenants/acmecorp/config.json` to define exactly what each supported role can access, then regenerate the policy file:

```json
{
  "name": "acmecorp",
  "roles": ["admin", "editor", "user", "viewer"],
  "permissions": {
    "owner": {
      "allowed_paths": ["/api/"],
      "denied_paths": [],
      "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]
    },
    "accountant": {
      "allowed_paths": ["/api/invoices/", "/api/reports/"],
      "denied_paths": ["/api/users/", "/api/admin/"],
      "allowed_methods": ["GET", "POST"]
    },
    "employee": {
      "allowed_paths": ["/api/timesheets/", "/api/profile/"],
      "denied_paths": ["/api/admin/"],
      "allowed_methods": ["GET", "POST", "PUT"]
    },
    "auditor": {
      "allowed_paths": ["/api/"],
      "denied_paths": ["/api/admin/"],
      "allowed_methods": ["GET"]
    }
  }
}
```

```bash
python3 scripts/tenant_manager.py sync-policies
```

**OPA reloads the generated file automatically** — no restart needed.

---

### Case 4: App Already Has Its Own Auth (wants to keep it)

**Client**: "We already handle login internally, we just want ZTAM to add HTTPS + rate limiting"

Solution: Disable auth enforcement for all routes (public mode):

In `tenants/<name>/config.json`, give the `public` role access to everything, regenerate `policies/tenants.json`, then disable ext_authz for that tenant route if you truly want public mode.

Or simpler: in `envoy/envoy.yaml`, find their virtual host and add `disabled: true` on the catch-all route:

```yaml
- match:
    prefix: "/"
  route:
    cluster: clientname_cluster
    timeout: 30s
  typed_per_filter_config:
    envoy.filters.http.ext_authz:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
      disabled: true
```

They get TLS, security headers, and rate-limiting (from Envoy), but NO JWT enforcement.

This should be treated as reduced-scope integration, not the main ZTAM model.

---

### Case 5: Client Has Existing Users In Its Own Database

If the customer wants existing usernames and passwords to keep working through ZTAM, you need an identity decision, not just a routing decision.

Recommended model:

- Keycloak remains the identity provider and session issuer
- Keycloak reads the client DB through the SPI using a read-only account
- password verification is done against the stored password hash
- ZTAM continues to enforce policy and forward trusted identity to the backend

Collect this before committing to DB-backed login:

- DB engine: MySQL or PostgreSQL
- host, port, and database name
- read-only DB username and password
- users table name
- username or email column
- password hash column
- role column if present
- hash algorithm, ideally bcrypt

If the client cannot provide those details, onboard the app first without DB federation or stop the auth integration until the contract is clear.

---

### Case 6: Multiple Apps for the Same Client (Sub-Apps)

**Client**: "We have a main app at `/` and an admin panel at `/admin/`"

Onboard them as two separate tenants, or use path-based routing within the same virtual host:

```yaml
routes:
  - match:
      prefix: "/admin/"
  route:
    cluster: acmecorp_admin_cluster
  - match:
      prefix: "/"
  route:
    cluster: acmecorp_main_cluster
```

---

### Case 6: Client Wants to Read User Identity in Their Backend

**No library needed.** Their backend just reads HTTP headers:

**Node.js (Express):**

```js
app.get("/api/dashboard", (req, res) => {
  const userId = req.headers["x-user-id"];
  const roles = req.headers["x-user-roles"]?.split(",");
  const tenantId = req.headers["x-tenant-id"];
  res.json({ message: `Hello user ${userId} with roles ${roles}` });
});
```

**Python (Flask/FastAPI):**

```python
@app.get("/api/dashboard")
def dashboard(request: Request):
    user_id  = request.headers.get("x-user-id")
    roles    = request.headers.get("x-user-roles", "").split(",")
    tenant   = request.headers.get("x-tenant-id")
    return {"user": user_id, "roles": roles}
```

**PHP:**

```php
$userId = $_SERVER['HTTP_X_USER_ID'];
$roles  = explode(',', $_SERVER['HTTP_X_USER_ROLES']);
```

---

## Permission Reference

### Generated `policies/tenants.json` Structure

```json
{
  "<tenant_id>": {
    "roles": {
      "<role_name>": {
        "allowed_paths":   ["/api/"],       ← path prefixes that ARE allowed
        "denied_paths":    ["/admin/"],     ← path prefixes that are BLOCKED
        "allowed_methods": ["GET", "POST"]  ← HTTP methods allowed
      }
    }
  }
}
```

### OPA Decision Logic

1. Is the user `admin`? → **ALLOW** (always)
2. Does `policies/tenants.json` have an entry for this tenant + role? → Use it
3. Does `policies/permissions.json` have an entry for this role? → Use it as fallback
4. None found → **DENY**

### Hot Reload

OPA reloads the generated file automatically — no restart required after `sync-policies`.

> [!TIP]
> **What about other Databases (PostgreSQL, Oracle, SQL Server)?**
> The current SPI is a template for MySQL. To support other DBs, you simply swap the JDBC driver in `keycloak-db-spi/pom.xml` and update the SQL queries in `MySqlUserStorageProvider.java`. The architecture remains identical for any database.

---

## Demo Day Playbook (Tomorrow)

You have a client app deployed at some URL. Here's the 10-minute demo:

### 1. Show the app unprotected

```bash
curl https://client-app.railway.app/api/tasks
# Returns data with no auth — insecure!
```

### 2. Run the onboarding script

```bash
./scripts/onboard-tenant.sh \
  --name     demo \
  --backend  https://client-app.railway.app \
  --hostname demo.yourdomain.com \
  --roles    "admin,user,viewer"
```

### 3. Show the app is now protected

```bash
# Without token → 401
curl https://demo.yourdomain.com/api/tasks

# With expired token → 403
curl -H "Authorization: Bearer <expired>" https://demo.yourdomain.com/api/tasks

# Get a valid token
TOKEN=$(curl -s -X POST https://demo.yourdomain.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"password"}' | jq -r .token)

# With valid token → 200, user data returned
curl -H "Authorization: Bearer $TOKEN" https://demo.yourdomain.com/api/tasks
```

### 4. Show role-based access (live, no restart)

Edit `tenants/demo/config.json`: remove `DELETE` from `user` role permissions, then run `python3 scripts/tenant_manager.py sync-policies`.

```bash
# As alice (user role) — DELETE now blocked instantly
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://demo.yourdomain.com/api/tasks/1
# → 403 Forbidden (OPA reloaded automatically, zero downtime)
```

---

## Files Reference

| File                             | Purpose                                                        |
| -------------------------------- | -------------------------------------------------------------- |
| `scripts/smoke_test_tenant.py`   | Acceptance smoke test for an onboarded tenant                  |
| `scripts/validate_deployment.py` | Production/deployment audit for env and TLS inputs             |
| `scripts/onboard-tenant.sh`      | Main onboarding automation                                     |
| `scripts/tenant_manager.py`      | Validates tenant configs and generates policies and Envoy data |
| `policies/tenants.json`          | Generated per-tenant role → permission mapping (hot-reload)    |
| `policies/permissions.json`      | Global fallback permissions                                    |
| `tenants/<name>/config.json`     | Source-of-truth tenant definition                              |
| `envoy/envoy.yaml`               | Envoy routing config with generated tenant sections            |

---

## Pricing / Tiers (Example Positioning)

| Tier           | Included                                                  |
| -------------- | --------------------------------------------------------- |
| **Starter**    | 1 tenant, ZTAM-managed Keycloak, up to 3 roles            |
| **Pro**        | 5 tenants, custom roles, live permission editing          |
| **Enterprise** | Unlimited tenants, custom domain, SLA, dedicated instance |

What justifies the price: eliminates weeks of auth/security plumbing, passes audits (HSTS, CSP, rate-limiting), single point of enforcement = one audit target.
