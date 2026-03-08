# ZTAM Tenant Change Policy

This document defines how tenant, policy, and routing changes should move from request to production.

## Purpose

Large companies do not treat access-policy changes as casual edits.

ZTAM should use the same model:

- tenant config is the source of truth
- every change is reviewed
- every change has validation evidence
- every change has a rollback path
- production releases are recorded

## Change Types

Classify each change before implementation.

### Standard Change

Low-risk change with a known pattern.

Examples:

- add a new tenant using an established template
- add a non-sensitive route permission
- correct a hostname typo before go-live

### Sensitive Change

Change that can alter effective access or public exposure.

Examples:

- widen access to admin routes
- change login mode
- change backend URL
- change public or protected path behavior
- edit generated routing behavior

### Emergency Change

Change needed to restore service or close a security hole immediately.

Examples:

- block a path that is incorrectly exposed
- roll back a faulty tenant permission change
- disable a broken tenant integration during an incident

## Required Inputs For Any Change

Before opening a change, capture:

1. Tenant name
2. Requested outcome
3. Business justification
4. Files expected to change
5. Risk level: standard, sensitive, or emergency
6. Validation plan
7. Rollback plan

## Source-Of-Truth Rules

- Edit tenant intent in `tenants/<name>/config.json`.
- Treat generated policy and Envoy sections as outputs, not primary inputs.
- Regenerate outputs using the supported scripts.
- Do not hand-edit generated tenant sections in `envoy/envoy.yaml` as the final source of truth.

## Review Policy

### Standard Change

Minimum requirements:

- one reviewer
- validation evidence attached
- rollback described

### Sensitive Change

Minimum requirements:

- one platform reviewer
- one application owner or tenant approver
- smoke-test evidence for the affected role or route
- explicit confirmation that blast radius is understood

### Emergency Change

Minimum requirements:

- implement and validate immediately
- record why normal review was bypassed
- complete retrospective review after stabilization

## Required Validation Evidence

Every tenant or policy change should include the relevant evidence.

Core checks:

- `python3 scripts/tenant_manager.py validate`
- `python3 scripts/tenant_manager.py sync-policies`
- `python3 scripts/tenant_manager.py sync-envoy` when routing changes
- `python3 scripts/validate_deployment.py --env-file .env --cert-dir envoy/certs --production` for release-bound changes

Runtime checks as applicable:

- smoke test for authenticated admin access
- smoke test for restricted-user denial
- hosted-Keycloak redirect check when login mode is `keycloak`
- spoofed-header denial confirmation when auth behavior changed

## Promotion Path

Use the same progression for every non-emergency change.

1. Update tenant config and any supporting docs.
2. Run validation locally.
3. Open a pull request using the repository template.
4. Attach test evidence and rollback notes.
5. Merge only after review requirements are satisfied.
6. Regenerate outputs in the release candidate.
7. Run go-live checks for production-bound changes.
8. Record deployment date, operator, and outcome.

## Rollback Procedure

Every change must be reversible.

Standard rollback model:

1. Revert the change in source control.
2. Re-run `python3 scripts/tenant_manager.py sync-policies`.
3. Re-run `python3 scripts/tenant_manager.py sync-envoy` if routing changed.
4. Reload or restart Envoy.
5. Re-run the minimum smoke test for the affected tenant.
6. Record that rollback occurred and why.

Emergency containment option:

- temporarily narrow permissions or disable the affected tenant route while a full rollback is prepared

## Release Record

For production changes, store a simple release note with:

- tenant name
- change summary
- approvers
- commands executed
- smoke-test result
- rollback status
- deployment timestamp

This can live in the pull request description, release ticket, or operating log, but it must exist.

## Ownership Model

- Platform team owns gateway, middleware, policy generation, and release process.
- Tenant or application owner owns route intent, role meaning, and business approval.
- No access-model change should be treated as complete without both technical and business accountability.

## Minimum Enterprise Standard

ZTAM should consider a tenant change production-ready only when:

- the change came through review
- validation evidence exists
- rollback is documented
- the affected tenant was re-tested
- the release record is captured
