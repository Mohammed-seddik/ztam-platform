# ZTAM Observability And Incident Runbook

This document defines the minimum logging, metrics, dashboards, alerts, and incident flow needed to operate ZTAM like a platform service.

## Purpose

ZTAM is not only an integration stack. In production it becomes a control point for authentication, authorization, and routing.

That means operators must be able to answer these questions quickly:

- Are users failing to authenticate?
- Are requests being denied intentionally or by mistake?
- Did a tenant change break routing?
- Is Keycloak, OPA, Envoy, or the backend failing?
- What changed before the incident started?

## Core Runtime Signals

Collect signals from these components.

### Envoy

Use Envoy access logs to see:

- request path and method
- downstream host
- response code
- upstream service selected
- latency
- request identifiers

### Auth Middleware

Current middleware logs already emit important events such as:

- login success and failure
- JWT validation failures
- missing token behavior
- OPA failures
- allow and deny decisions

The auth middleware now emits structured event logs and request IDs. Run it with `LOG_FORMAT=json` so the output stays machine-readable in production collectors.
It also exposes a Prometheus-style metrics endpoint at `/metrics` for request volume, request latency, login outcomes, authorization decisions, and failure counters.

### OPA

Capture policy decision timing and policy evaluation failures.

### Keycloak

Enable event logging for:

- login success
- login failure
- logout
- admin changes

### Backend Applications

At minimum, applications should be able to correlate:

- protected hostname
- tenant identifier
- user identifier or username
- request identifier

ZTAM forwards `x-request-id` on allowed upstream requests so backend logs can be correlated with gateway and auth-middleware events.

## Required Log Fields

Where possible, standardize around these fields:

- `timestamp`
- `service`
- `environment`
- `tenant_id`
- `request_id`
- `username`
- `user_id`
- `roles`
- `path`
- `method`
- `decision`
- `reason`
- `status_code`
- `upstream_service`
- `latency_ms`

## Minimum Metrics

Track these counters or rates by service and tenant where practical.

Current auth-middleware baseline:

- `ztam_auth_http_requests_total`
- `ztam_auth_http_request_duration_seconds`
- `ztam_auth_login_attempts_total`
- `ztam_auth_decisions_total`
- `ztam_auth_failures_total`

For local review, the repository now includes an optional Docker Compose `observability` profile with:

- Prometheus on `127.0.0.1:9090`
- Grafana on `127.0.0.1:3001`

Start it with:

```bash
docker compose --profile observability up -d prometheus grafana
```

Grafana auto-provisions:

- a Prometheus datasource
- a starter dashboard named `ZTAM Overview`

Authentication metrics:

- login success count
- login failure count
- rate-limit hit count
- callback/token-exchange error count

Authorization metrics:

- allow count
- deny count
- deny reason breakdown
- OPA error count

Gateway metrics:

- upstream 5xx rate
- upstream 4xx rate
- request latency percentiles
- request volume by tenant and route

Identity-provider metrics:

- Keycloak availability
- token endpoint latency
- JWKS fetch failures

## Dashboard Requirements

The main operating dashboard should show:

1. Total request volume
2. Auth success and failure trend
3. Deny rate trend
4. Upstream 5xx trend
5. Tenant-level breakdown for failures
6. Top failing routes
7. Recent deployment or tenant-change markers

## Alert Requirements

Create alerts for at least these cases:

1. sustained spike in login failures
2. sustained spike in access denies for a tenant
3. OPA unreachable or decision failures
4. Keycloak unavailable or token-exchange failure spike
5. upstream 5xx spike after a release
6. gateway health degradation

Each alert should include:

- affected tenant if known
- affected service
- start time
- recent deployment reference if available
- first-response steps

## Incident Triage Flow

When an access incident is reported, work in this order.

1. Confirm the tenant, route, and affected user role.
2. Check whether a tenant or policy change was released recently.
3. Check Envoy status and recent response codes.
4. Check auth-middleware logs for token, login, callback, or OPA failures.
5. Check Keycloak availability and event logs.
6. Check whether the backend is healthy and reachable.
7. Decide whether to roll forward, roll back, or contain.

## First-Response Playbooks

### Symptom: Users cannot log in

Check:

- Keycloak reachability
- client secret and issuer configuration
- callback URL correctness
- recent login-mode changes

Containment:

- roll back recent auth config changes
- temporarily block faulty tenant onboarding if only one tenant is affected

### Symptom: Users are unexpectedly denied

Check:

- recent permission edits
- route and method matching
- role mapping
- OPA response path

Containment:

- revert the permission change
- confirm deny reason from logs before widening access

### Symptom: Backend is returning errors after gateway approval

Check:

- upstream health
- forwarded identity headers
- backend route assumptions
- hostname and redirect correctness

Containment:

- roll back routing changes
- move traffic away from the affected tenant route if needed

## Change Correlation

Every production tenant change should be linkable to:

- a pull request or change request
- validation evidence
- deployment timestamp
- operator identity

Without this, incidents take longer to diagnose and audit.

## Current Repo Baseline

Today the repo already provides:

- auth-middleware runtime logs
- request ID generation and propagation via `x-request-id`
- smoke tests for login behavior
- deployment validation script
- tenant source-of-truth model
- documented go-live checks

The next implementation step after this document is to export service metrics in a standard format and extend the same logging discipline across the rest of the stack.
