# ZTAM Demo Presentation Guide

This file is the fastest way to present the project tomorrow without improvising the whole story live.

## 1. One-Sentence Pitch

ZTAM is a Zero Trust access platform that places an existing web application behind Envoy, Keycloak, and OPA so authentication, authorization, tenant routing, and auditability are centralized instead of being reimplemented in every client application.

## 2. What To Show First

Start with the business problem:

- many legacy or internal apps have weak or inconsistent access control
- companies want centralized authentication and authorization
- rewriting every application is expensive
- ZTAM protects these applications at the gateway layer with minimal app changes

Then explain the architecture in one pass:

1. User reaches Envoy.
2. Envoy sends auth decisions to auth-middleware.
3. auth-middleware validates identity with Keycloak.
4. auth-middleware asks OPA if access should be allowed.
5. If allowed, the request is forwarded to the application.

## 3. Demo Flow

Use this order.

### Step A: Show the running platform

Open:

- `https://localhost`
- `http://127.0.0.1:9090`
- `http://127.0.0.1:3001`

Say:

"This is not just one application. It is a platform made of gateway enforcement, centralized identity, policy control, tenant configuration, and observability."

### Step B: Show authentication and authorization

Use the bundled demo users:

- `alice / $DEMO_ALICE_PASSWORD` as admin
- `charlie / $DEMO_CHARLIE_PASSWORD` as limited user

Show:

1. unauthenticated access goes to login
2. admin can access all task data
3. limited user can only access their own task view and permitted actions

Say:

"The application itself does not need to reimplement the whole Zero Trust stack. The platform enforces identity and policy before the request reaches the backend."

### Step C: Show multi-tenant onboarding model

Show these files and explain them briefly:

- `tenants/<name>/config.json`
- `scripts/tenant_manager.py`
- `policies/tenants.json`
- `envoy/envoy.yaml`

Say:

"The editable source of truth is the tenant config. Policy and routing are generated from it so onboarding is repeatable and not manual."

If the teacher asks how a new client website is integrated, run:

```bash
python3 scripts/tenant_manager.py assess \
   --backend-url https://app.customer.com \
   --name newclient \
   --hostname newclient.ztam.local \
   --roles "admin,editor,user,viewer" \
   --write-config
```

Then say:

"This gives me a first-pass integration verdict, detects whether the site still has its own login behavior, recommends the ZTAM login mode, and generates the starter tenant config dynamically."

### Step D: Show enterprise controls

Show these documents:

- `INTEGRATION_CONTRACT.md`
- `TENANT_CHANGE_POLICY.md`
- `OBSERVABILITY_RUNBOOK.md`
- `ENTERPRISE_ROADMAP.md`

Say:

"The project is not only a prototype stack. It now includes governance, rollout discipline, observability, validation, and an enterprise roadmap."

### Step E: Show observability

In Prometheus, mention that auth-middleware exports metrics.

In Grafana, open `ZTAM Overview`.

Say:

"I added structured logs, request correlation, metrics, Prometheus scraping, and an auto-provisioned Grafana dashboard. That moves the project closer to how a real platform team would operate it."

## 4. Commands To Prepare Before The Demo

Run these before presenting if needed:

```bash
docker compose up -d --build
docker compose --profile observability up -d prometheus grafana
python3 demo/setup_demo.py --force
python3 scripts/tenant_manager.py validate
python3 scripts/tenant_manager.py assess --backend-url https://app.customer.com
./scripts/demo_teacher_flow.sh
```

Optional quick health checks:

```bash
docker compose ps
curl -k https://localhost
curl -fsS http://127.0.0.1:9090/-/ready
curl -I -s http://127.0.0.1:3001/login | head -n 1
```

## 5. Two-Minute Rehearsal Checklist

Do this once before you sleep and once tomorrow before the teacher arrives.

1. Open `https://localhost` and confirm the login page appears.
2. Log in with `alice / $DEMO_ALICE_PASSWORD` and confirm the app loads.
3. Open Grafana at `http://127.0.0.1:3001` and confirm `ZTAM Overview` exists.
4. Open Prometheus at `http://127.0.0.1:9090` and confirm it is ready.
5. Keep these files ready in the editor:
   - `ENTERPRISE_ROADMAP.md`
   - `INTEGRATION_CONTRACT.md`
   - `TENANT_CHANGE_POLICY.md`
   - `OBSERVABILITY_RUNBOOK.md`
   - `tenants/testapp/config.json`

## 6. If Something Fails Live

Do not explain the whole failure. Recover fast and continue the story.

### If the app page is slow or broken

Run:

```bash
docker compose ps
docker compose restart envoy auth-middleware
```

Then say:

"The important point is that the platform components are separated, so I can check and recover the gateway and auth layer independently from the application."

### If Grafana looks empty

Show Prometheus first at `http://127.0.0.1:9090` and say:

"The metrics endpoint is working and Prometheus is scraping it. Grafana is only the visualization layer on top."

Then refresh Grafana.

### If login does not work immediately

Show the architecture and tenant files first, then return to the app.

Say:

"Even if the live login needs a restart, the core design remains the same: Envoy enforces entry, auth-middleware validates identity, and OPA decides access."

## 7. Suggested 5-Minute Script

Use something close to this:

1. "My project is a Zero Trust access platform for protecting existing web applications."
2. "Instead of putting authentication and authorization logic inside every app, I centralize it with Envoy, Keycloak, OPA, and an auth middleware service."
3. "A request first reaches Envoy, then auth-middleware validates the token and asks OPA for the policy decision before the backend receives traffic."
4. "The platform is multi-tenant. Each tenant has a config file, and routing and policy outputs are generated from that source of truth."
5. "I also added enterprise-oriented features: change governance, validation gates, observability, Prometheus metrics, and a Grafana dashboard."
6. "So the project is not only a demo login flow. It is closer to a platform foundation that could be extended into a real enterprise access service."

## 8. Likely Teacher Questions

### What is the main innovation here?

Answer:

The main value is protecting existing applications without rewriting each one, while centralizing identity, policy, and tenant onboarding.

### Did you modify the client application heavily?

Answer:

No. The goal is to minimize application changes. Some integration assumptions still exist, which is why I added an explicit integration contract instead of claiming any URL can be protected automatically.

### Why is this more than just a reverse proxy?

Answer:

Because it is not only routing. It validates identity, enforces policy decisions, injects trusted identity context, supports tenant-aware behavior, and now includes governance and observability.

### What makes it closer to enterprise style?

Answer:

The roadmap, CI validation, tenant change policy, integration contract, structured logs, metrics endpoint, Prometheus scraping, and Grafana dashboard.

### What is still missing?

Answer:

The next major steps are production reference architecture, stronger deployment topology, and eventually a real control-plane API or UI for tenant lifecycle management.

### What happens if a client brings a random website tomorrow?

Answer:

I can assess the site first with `tenant_manager.py assess`, generate the starter tenant config dynamically, then validate login and RBAC with the smoke test. If the assessment says `needs-review`, that means the site still has integration assumptions like its own session login or redirect behavior, so I can explain exactly what needs adapting instead of guessing.

## 9. If Time Is Short

If you only have 2 minutes, show this:

1. `https://localhost`
2. a successful login
3. Grafana dashboard
4. `tenants/testapp/config.json`
5. `ENTERPRISE_ROADMAP.md`

That is enough to show architecture, protection, tenant model, and platform maturity.

If you want a single command for the whole teacher flow, use:

```bash
./scripts/demo_teacher_flow.sh
```

If a client gives you a different site live, pass it in as the first argument:

```bash
./scripts/demo_teacher_flow.sh https://client-site.example.com
```
