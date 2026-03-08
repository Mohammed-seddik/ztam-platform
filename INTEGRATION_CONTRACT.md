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
