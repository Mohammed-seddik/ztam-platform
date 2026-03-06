# ZTAM Integration Guide
## Selling & Onboarding Any Client App — Zero Code Required

---

## What Is ZTAM?

ZTAM (Zero Trust Access Management) sits in front of **any web app** and handles:

| What we provide | What the client needs to do |
|---|---|
| SSL/TLS termination (HTTPS) | Nothing |
| Login / logout via Keycloak | Nothing |
| JWT validation on every request | Nothing |
| Role-based access control (OPA) | Define their roles once |
| Rate-limiting & security headers | Nothing |
| User identity headers to backend | Optionally read them |

The client's app **does not need to change its code**. It just sits behind ZTAM and receives trusted HTTP headers on every request.

---

## The 5-Minute Pitch

> "Your app is currently unprotected. Anyone can hit your API directly.
> We put our service in front of it — all traffic goes through a TLS-terminating
> reverse proxy that validates JWTs, enforces role-based access, and forwards
> clean user-identity headers to your backend.
> No code changes. DNS change + 30-second config. Done."

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
- **Permissions entry** in `policies/tenants.json`

---

## Onboarding a New Client — Step by Step

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
  --roles    "admin,manager,user,viewer"
```

That's it. The script:
1. Creates a Keycloak client `myapp` with the specified roles
2. Adds permissions to `policies/tenants.json` (OPA picks them up in < 1s)
3. Adds an Envoy virtual host and cluster for `myapp.railway.app`
4. Saves the tenant config to `tenants/myapp/config.json`
5. Reloads Envoy

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

**Client**: "We have 4 roles: `owner`, `accountant`, `employee`, `auditor`"

```bash
./scripts/onboard-tenant.sh \
  --name     acmecorp \
  --backend  https://app.acmecorp.com \
  --hostname acmecorp.yourdomain.com \
  --roles    "admin,owner,accountant,employee,auditor"
```

Then customize `policies/tenants.json` to define exactly what each role can access:

```json
{
  "acmecorp": {
    "roles": {
      "owner": {
        "allowed_paths":   ["/api/"],
        "denied_paths":    [],
        "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]
      },
      "accountant": {
        "allowed_paths":   ["/api/invoices/", "/api/reports/"],
        "denied_paths":    ["/api/users/", "/api/admin/"],
        "allowed_methods": ["GET", "POST"]
      },
      "employee": {
        "allowed_paths":   ["/api/timesheets/", "/api/profile/"],
        "denied_paths":    ["/api/admin/"],
        "allowed_methods": ["GET", "POST", "PUT"]
      },
      "auditor": {
        "allowed_paths":   ["/api/"],
        "denied_paths":    ["/api/admin/"],
        "allowed_methods": ["GET"]
      }
    }
  }
}
```

**OPA reloads the file automatically** — no restart needed.

---

### Case 4: App Already Has Its Own Auth (wants to keep it)

**Client**: "We already handle login internally, we just want ZTAM to add HTTPS + rate limiting"

Solution: Disable auth enforcement for all routes (public mode):

In `policies/tenants.json`, give the `public` role access to everything, then in Envoy's virtual host for this tenant, disable ext_authz globally.

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

---

### Case 5: Multiple Apps for the Same Client (Sub-Apps)

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
app.get('/api/dashboard', (req, res) => {
  const userId   = req.headers['x-user-id'];
  const roles    = req.headers['x-user-roles']?.split(',');
  const tenantId = req.headers['x-tenant-id'];
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

### `policies/tenants.json` Structure

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

OPA watches `/policies` with `--watch`. Any change to `tenants.json` or `permissions.json` takes effect **within 1 second** — no restart required. Safe to edit live.

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

Edit `policies/tenants.json`: remove `DELETE` from `user` role → save.

```bash
# As alice (user role) — DELETE now blocked instantly
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://demo.yourdomain.com/api/tasks/1
# → 403 Forbidden (OPA reloaded automatically, zero downtime)
```

---

## Files Reference

| File | Purpose |
|---|---|
| `scripts/onboard-tenant.sh` | Main onboarding automation |
| `scripts/opa_add_tenant.py` | Adds tenant entry to `policies/tenants.json` |
| `scripts/envoy_add_tenant.py` | Adds Envoy vhost + cluster for the new backend |
| `policies/tenants.json` | Per-tenant role → permission mapping (hot-reload) |
| `policies/permissions.json` | Global fallback permissions |
| `tenants/<name>/config.json` | Saved metadata per onboarded tenant |
| `envoy/envoy.yaml` | Envoy routing config (modified by onboard script) |

---

## Pricing / Tiers (Example Positioning)

| Tier | Included |
|---|---|
| **Starter** | 1 tenant, ZTAM-managed Keycloak, up to 3 roles |
| **Pro** | 5 tenants, custom roles, live permission editing |
| **Enterprise** | Unlimited tenants, custom domain, SLA, dedicated instance |

What justifies the price: eliminates weeks of auth/security plumbing, passes audits (HSTS, CSP, rate-limiting), single point of enforcement = one audit target.
