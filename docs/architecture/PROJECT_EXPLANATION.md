# ZTAM Project Explanation

This document explains the whole project in one place: what it is, what it can do, how it works, what each major folder is for, and where the current boundaries are.

## 1. What This Project Is

ZTAM stands for Zero Trust Application Middleware.

The project is a security gateway that sits in front of an existing web application and adds:

- centralized authentication with Keycloak
- centralized authorization with OPA
- HTTPS termination and reverse proxying with Envoy
- multi-tenant onboarding and routing
- identity forwarding to backend applications
- repeatable operational workflows for onboarding, validation, and go-live

The main business idea is simple:

Instead of rewriting every client application to implement modern access control correctly, ZTAM puts a security and identity layer in front of the app.

That means a customer application can stay mostly unchanged while ZTAM handles:

- who the user is
- whether the user may access a route
- what headers or token the backend receives
- how login is presented to the browser

In short, this repo is not just a demo app. It is a reusable platform for protecting web applications.

## 2. What The Project Can Do

ZTAM can currently do all of the following.

- Protect an existing web app behind a single secure HTTPS entrypoint.
- Redirect all plain HTTP traffic to HTTPS.
- Authenticate users through Keycloak.
- Validate signed JWTs using Keycloak JWKS.
- Authorize requests with OPA using route and method based RBAC.
- Support multiple tenants, each with its own hostname, backend, roles, and permissions.
- Support two login modes:
  - app-owned login form mode
  - platform-owned Keycloak login mode
- Forward trusted user identity to the backend only after authentication and authorization succeed.
- Generate routing and policy data from tenant config files.
- Onboard and offboard tenants with scripts instead of manual config edits.
- Smoke-test tenant behavior before go-live.
- Run a deployment audit against env vars and TLS files.
- Package a working local demo environment with a sample protected application.

## 3. The Core Product Idea

ZTAM is designed for the case where an organization has one or more applications that were not originally built with a modern centralized identity and authorization layer.

Examples:

- a legacy internal business app
- a customer-hosted web app with weak auth controls
- a simple application that has only local login logic
- multiple customer apps that all need different role rules but the same access platform

ZTAM lets you place those apps behind a consistent access model.

That model is:

1. Every browser request reaches Envoy first.
2. Envoy decides whether the request must be authenticated.
3. Auth middleware validates the user token and asks OPA for a policy decision.
4. If allowed, the request is forwarded to the backend with trusted identity context.
5. If denied, the request stops before reaching the app.

This gives a central control plane for identity and access instead of spreading security logic across many separate applications.

## 4. Main Components

### Envoy

Envoy is the public gateway.

Its job is to:

- terminate TLS
- redirect HTTP to HTTPS
- route requests to the right upstream service
- call the auth middleware through `ext_authz`
- attach security headers to responses
- isolate internal services from direct public exposure

Only the gateway is supposed to be the normal user-facing entrypoint.

### Auth Middleware

The auth middleware is implemented with FastAPI in [services/auth-middleware/main.py](/home/mohammed-seddik/PFE%20Folder/ztam-platform/services/auth-middleware/main.py).

It is the central runtime decision service.

Its responsibilities include:

- serving the platform login page
- handling login submission
- redirecting to Keycloak when needed
- validating Keycloak-issued JWTs
- extracting roles from token claims
- finding the active tenant from the request hostname
- calling OPA for authorization
- returning trusted identity headers to Envoy
- enforcing login rate limiting

This service is effectively the runtime brain of the platform.

### Keycloak

Keycloak is the identity provider.

It handles:

- user authentication
- session handling
- client configuration
- token issuance
- realm configuration

For the bundled demo, Keycloak can authenticate against the demo app database through a custom Java SPI.

### OPA

OPA is the policy engine.

Its rules live in [policies/authz.rego](/home/mohammed-seddik/PFE%20Folder/ztam-platform/policies/authz.rego).

OPA receives structured input about:

- the user
- the tenant
- the request path
- the request method

It then answers one question: allow or deny.

ZTAM uses OPA to keep authorization logic outside the application itself.

### Java SPI

The Java SPI under [keycloak-db-spi](/home/mohammed-seddik/PFE%20Folder/ztam-platform/keycloak-db-spi) extends Keycloak so that Keycloak can authenticate users against the demo app's MySQL user table.

This is useful because it proves ZTAM can bridge modern identity tooling with an app that already has its own user database.

### Demo App

The sample protected application lives under [demo/testapp](/home/mohammed-seddik/PFE%20Folder/ztam-platform/demo/testapp).

It is a Node.js + MySQL task application used to demonstrate the platform.

It exists to prove the platform works end-to-end, but it is not the platform itself.

## 5. High-Level Request Flow

At a high level, a normal protected request works like this.

1. The browser sends a request to the ZTAM hostname.
2. Envoy receives the request.
3. If the route is public, Envoy forwards it directly.
4. If the route is protected, Envoy pauses and calls the auth middleware.
5. The auth middleware checks for a valid token.
6. The auth middleware validates the JWT against Keycloak JWKS.
7. The auth middleware extracts user identity and roles.
8. The auth middleware calls OPA.
9. OPA returns allow or deny.
10. If allowed, Envoy forwards the request to the backend with trusted identity headers.
11. If denied, the backend never sees the request.

This is the core Zero Trust behavior of the system.

## 6. Login Modes

ZTAM supports two distinct onboarding patterns.

### Form Mode

Use this when the app already has a login form or login-like flow and you want the platform to preserve the user experience while centralizing authentication.

In this mode:

- the browser submits login credentials to a ZTAM-routed endpoint
- the auth middleware sends those credentials to Keycloak
- Keycloak authenticates the user
- the platform returns a successful login result back into the app flow

This mode is supported for compatibility and selective onboarding cases where the client must preserve an existing login UX.

### Keycloak Mode

Use this when the backend app should not own login at all, or when the app does not have a usable login page.

In this mode:

- unauthenticated users are redirected into a platform-owned login experience
- Keycloak handles the actual identity flow
- ZTAM sends the user back to the protected application after authentication

This is the better fit for older or thinner applications.

## 7. Tenant Model

The tenant system is one of the most important parts of the repo.

Each tenant has a config file in `tenants/<name>/config.json`.

A tenant config defines:

- tenant name
- display name
- backend URL
- protected hostname
- Keycloak client ID
- Keycloak realm
- login mode
- whether SPI integration is used
- role list
- per-role permissions

Example tenants already in the repo:

- [tenants/testapp/config.json](/home/mohammed-seddik/PFE%20Folder/ztam-platform/tenants/testapp/config.json)

The current source-of-truth model is:

- tenant config files are the editable inputs
- generated Envoy config blocks are outputs
- generated tenant policy data is an output

This avoids hand-editing multiple files for every customer app.

## 8. Tenant Manager

The central tool for tenant lifecycle management is [scripts/tenant_manager.py](/home/mohammed-seddik/PFE%20Folder/ztam-platform/scripts/tenant_manager.py).

It can:

- validate tenant configs
- list tenant configs
- normalize roles and permissions
- create or update tenant configs
- delete tenant configs
- generate OPA tenant data
- generate Envoy tenant route and cluster blocks

This makes onboarding reproducible.

Instead of editing:

- Envoy routes manually
- OPA tenant data manually
- customer metadata manually in multiple places

you update the tenant config and regenerate outputs.

## 9. Authorization Model

The authorization rules live in [policies/authz.rego](/home/mohammed-seddik/PFE%20Folder/ztam-platform/policies/authz.rego).

The important behavior is:

- `admin` always has full access
- non-admin roles are checked against role permissions
- tenant-specific permissions are used first
- if tenant-specific permissions do not exist, the policy falls back to global defaults

The policy currently focuses on route and method RBAC.

That means rules are based on:

- the current tenant
- the user roles
- the request path
- the HTTP method

This is enough to support practical multi-tenant role enforcement across different applications.

## 10. Demo Environment

The demo assets are now intentionally separated under [demo](/home/mohammed-seddik/PFE%20Folder/ztam-platform/demo), as described in [demo/README.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/demo/README.md).

That directory contains:

- the sample app
- the demo bootstrap script
- the demo helper shell script

The point of the demo is to let someone run the whole stack locally and observe:

- Keycloak setup
- protected routing
- login behavior
- OPA decisions
- backend access after successful policy checks

The root wrappers `setup_demo.py` and `demo_test.sh` exist only to preserve older commands.

## 11. Operational Tooling

This repo includes operator-facing workflows, not just code.

### Onboarding

[scripts/onboard-tenant.sh](/home/mohammed-seddik/PFE%20Folder/ztam-platform/scripts/onboard-tenant.sh) helps create a new tenant and integrate it into the platform.

### Offboarding

[scripts/offboard-tenant.sh](/home/mohammed-seddik/PFE%20Folder/ztam-platform/scripts/offboard-tenant.sh) removes a tenant configuration and regenerates outputs.

### Smoke Testing

[scripts/smoke_test_tenant.py](/home/mohammed-seddik/PFE%20Folder/ztam-platform/scripts/smoke_test_tenant.py) validates tenant behavior.

It can test:

- login behavior
- protected route behavior
- role-based access behavior
- pre-DNS validation using a host header override

### Deployment Audit

[scripts/validate_deployment.py](/home/mohammed-seddik/PFE%20Folder/ztam-platform/scripts/validate_deployment.py) checks whether the deployment inputs are consistent.

It audits things like:

- required env vars
- public URLs
- TLS files
- cookie settings
- placeholder values

This is important because many failures in gateway-based systems are deployment mistakes rather than code bugs.

## 12. Documentation For Operators And Delivery

The repo includes several docs that turn the project into something closer to a product package.

- [README.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/README.md): top-level technical entrypoint
- [ARCHITECTURE.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/architecture/ARCHITECTURE.md): component and request flow explanation
- [INTEGRATION_GUIDE.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/operators/INTEGRATION_GUIDE.md): customer/app integration guidance
- [ONBOARDING_PLAYBOOK.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/operators/ONBOARDING_PLAYBOOK.md): operator workflow for new tenants
- [DEPLOYMENT.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/operators/DEPLOYMENT.md): deployment guidance
- [GO_LIVE_CHECKLIST.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/operators/GO_LIVE_CHECKLIST.md): final release gate
- [CUSTOMER_HANDOFF_TEMPLATE.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/operators/CUSTOMER_HANDOFF_TEMPLATE.md): customer delivery summary template
- [EXECUTION_PLAN.md](/home/mohammed-seddik/PFE%20Folder/ztam-platform/docs/product/EXECUTION_PLAN.md): roadmap and completion state

This matters because the project is trying to be sellable and operable, not just technically interesting.

## 13. Security Positioning

The repo follows a gateway-centric security model.

Important characteristics:

- public traffic enters through Envoy
- internal services are not the main public entrypoints
- authentication is delegated to Keycloak
- authorization is delegated to OPA
- tenant routing is explicit and generated
- identity trust is established centrally, not by trusting client-supplied headers

This is the correct direction for a Zero Trust access layer.

It lets backend applications stay simpler while moving trust decisions into a controlled perimeter service.

## 14. Current Strengths

The project is already strong in these areas.

- Clear separation between platform code and demo assets.
- Real multi-tenant model instead of hardcoded single-app behavior.
- Repeatable onboarding and offboarding workflow.
- Centralized policy and routing generation.
- Practical operator documentation.
- Working local demonstration path.
- Support for both form-based and Keycloak-hosted login experiences.
- Production-oriented deployment audit and go-live checklist.

These are the parts that make the project feel like a platform rather than only a classroom demo.

## 15. Current Limits

The current repo is solid, but not everything is fully product-complete.

The main current limits are:

- production readiness still depends on correct real `.env` values, DNS, and TLS
- CI validation for tenant configs and smoke-test syntax is still listed as later work
- there is not yet a dedicated hosted-Keycloak sample tenant packaged as a first-class example
- device trust is not fully integrated into a real external posture source
- there is no separate management UI for tenant administration; the workflow is still script and config driven

These are product maturity items, not foundational design failures.

## 16. What This Project Is Best For

ZTAM is best suited for:

- protecting legacy or existing web apps without invasive code changes
- standardizing access control across multiple customer applications
- demonstrating a Zero Trust gateway pattern in a portfolio, academic, or client context
- building a reusable security perimeter around heterogeneous apps

It is especially valuable when you need one platform team to control access for many apps with different business roles and routing requirements.

## 17. Repo Structure Summary

At a high level, the repo breaks down like this.

- `services/`: runtime platform services, especially auth middleware
- `envoy/`: gateway and TLS configuration
- `policies/`: OPA policy and generated tenant data
- `scripts/`: tenant lifecycle, smoke testing, and deployment validation
- `tenants/`: source-of-truth per-tenant configs
- `keycloak-db-spi/`: Java Keycloak extension
- `demo/`: bundled demo application and demo tooling

This structure is now clean and intentional.

## 18. Bottom Line

The project is a multi-tenant Zero Trust access platform that places a secure identity and authorization layer in front of existing web applications.

Its real value is not the sample app.

Its real value is the combination of:

- Envoy as the enforcement gateway
- Keycloak as identity provider
- OPA as authorization engine
- auth middleware as the runtime coordinator
- tenant configs as the source of truth
- scripts and docs as the operational model

If you describe the project in one sentence, the best summary is:

ZTAM is a reusable platform that can take an existing web app, put it behind centralized Zero Trust access controls, and make that integration repeatable across multiple tenants and customer applications.
