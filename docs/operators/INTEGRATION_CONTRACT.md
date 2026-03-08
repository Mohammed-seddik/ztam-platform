# ZTAM Integration Contract

This document defines what an application must support in order to be onboarded behind ZTAM with low integration friction.

## Purpose

ZTAM is designed to protect existing web applications with minimal code changes.

It is not a promise that any arbitrary URL can be secured instantly with zero adaptation.

The correct platform promise is:

ZTAM can protect many existing web applications with limited application changes, provided the app fits the supported reverse-proxy and identity assumptions below.

## Required Application Assumptions

An application is a good fit for ZTAM when the following are true.

### 1. Network Reachability

- The backend is reachable from the ZTAM gateway.
- The backend can accept traffic from Envoy.
- The backend does not require direct public exposure after onboarding.

### 2. Reverse Proxy Compatibility

- The application can run behind a reverse proxy.
- The application does not break when accessed through the protected hostname.
- The application can tolerate forwarded headers and proxy routing.

### 3. Predictable Routing

- The protected paths are known.
- The public paths are known.
- Route-level authorization can be described with path and method rules.

### 4. Login Compatibility

The application must fit one of these patterns:

- it already has a login flow that ZTAM can intercept cleanly
- it can rely on a platform-hosted login flow

### 5. Identity Consumption

- The application can accept trusted identity context from ZTAM.
- If the application expects a specific token or header format, that contract must be defined during onboarding.

## Supported Integration Patterns

ZTAM does not assume every application consumes identity the same way.

Large companies usually standardize a small set of supported patterns and then classify each client app into one of them.

ZTAM should be operated the same way.

### Pattern 1: Trusted Header Integration

This is the preferred and most scalable pattern.

What the app does:

- trusts identity headers set by ZTAM
- does not trust the browser to set those headers
- uses those headers to resolve the current user, roles, and tenant

Headers:

- `x-user-id`
- `x-username`
- `x-user-roles`
- `x-tenant-id`
- optional `x-request-id`

Client-side changes:

- point the protected hostname to ZTAM
- update redirects to use the protected hostname
- read trusted headers in the backend or middleware

Best fit:

- internal apps
- server-rendered apps
- apps where backend auth can be adapted lightly

### Pattern 2: Direct OIDC / Keycloak-Aware App

This is common in mature application teams.

What the app does:

- integrates with Keycloak or OIDC itself
- understands access tokens, sessions, callbacks, or userinfo directly

Client-side changes:

- configure the app for Keycloak/OIDC
- align callback URLs and logout URLs with the protected hostname
- decide which auth responsibilities stay in the app vs ZTAM

Best fit:

- apps already built around OIDC
- modern SPAs and web apps with native IdP support

### Pattern 3: Adapter / Token Translation

This is the legacy compatibility pattern.

What the app does:

- keeps expecting a local token format or specific auth contract
- ZTAM translates validated identity into that downstream format

Client-side changes:

- define the exact downstream token or session contract
- accept a tenant-specific adapter configuration
- accept stronger validation and narrower support guarantees

Best fit:

- legacy apps
- apps that cannot easily read gateway headers
- demo or transition scenarios

### Pattern 4: Unsupported Without App Changes

Some apps are not good candidates without real integration work.

Examples:

- apps that break behind a reverse proxy
- apps with fragile hostname-bound cookies
- apps with deeply coupled custom auth/session internals
- apps that require direct browser-to-backend auth assumptions

In this case the correct platform answer is:

- document the gap
- list the required changes
- do not promise zero-effort onboarding

## What Leaders Usually Do

Leader-style platform teams normally use this order of preference:

1. trusted headers
2. direct OIDC support
3. adapter/translation path for legacy apps
4. reject or redesign unsupported apps

They do not pretend every application can be protected with zero changes.
They define standard patterns, classify the app, and state the required client changes clearly.

## Current ZTAM Status

Here is the honest status of this repository today.

### Already implemented

- Trusted header propagation from ZTAM to the backend
- Keycloak login page as the default browser login flow
- Adapter/token translation path for the bundled demo app
- Tenant classification model in the control-plane contract:
  - `managed_oidc`
  - `form_bridge`
  - `federated_db`

### Partially implemented

- Direct OIDC-aware application integration as a first-class app-owned contract
  - the platform uses Keycloak and OIDC flows already
  - but there is not yet a dedicated sample app proving a fully app-native OIDC integration

### Not yet first-class

- multiple packaged sample apps covering all integration patterns
- formal adapter plugin system for many token/session shapes
- production-grade self-service onboarding UI

## Common Client-Side Changes

Large companies normally define these as integration tasks, not as failures.

Typical client-side changes may include:

- updating DNS to point the protected hostname at ZTAM
- fixing hardcoded absolute URLs
- updating redirect logic to use the protected hostname
- adjusting CORS or origin allowlists
- adjusting cookie domain, secure, or same-site settings
- clarifying which routes are public and which are protected
- mapping business roles to route permissions

These are usually integration-level changes, not full application rewrites.

## Best-Fit Application Types

ZTAM is best suited for:

- server-rendered web apps
- internal business web apps
- legacy web applications
- customer applications that need centralized access control
- apps that can be described by hostname, routes, roles, and backend URL

## Higher-Risk Integrations

ZTAM is not automatically low-friction for every application.

Extra integration effort may be needed when the application:

- hardcodes internal URLs heavily
- depends on unusual session behavior
- uses fragile front-end redirect logic
- assumes direct browser-to-backend access patterns
- requires non-standard auth token shapes
- couples authorization deeply into internal app code paths

## Onboarding Decision Rule

Before onboarding a client application, confirm:

1. The backend is reachable by ZTAM.
2. The protected hostname and routing model are known.
3. The login mode is known.
4. The business roles are known.
5. The required route permissions are known.
6. Any client-side changes are explicitly listed and accepted.

If these are true, the app is a reasonable candidate for ZTAM.

## Enterprise Positioning

This is how large companies usually describe platforms like this:

- they do not promise instant protection for any URL
- they define a supported integration contract
- they standardize onboarding requirements
- they reduce application changes, but do not deny that some integration work may still be needed

ZTAM should be presented the same way.
