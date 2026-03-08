# ZTAM Enterprise Roadmap

This roadmap defines how ZTAM moves from a strong platform prototype to something closer to how large companies build and operate an internal access platform.

## Goal

Turn ZTAM into an enterprise-style access platform with:

- a clear application integration contract
- repeatable onboarding and release controls
- automated validation in CI
- auditable policy and tenant changes
- production-grade deployment guidance
- a clean separation between control-plane concerns and runtime enforcement

## Current Position

ZTAM already has the right architectural direction:

- gateway enforcement with Envoy
- centralized identity with Keycloak
- centralized authorization with OPA
- tenant-specific config as the source of truth
- onboarding, smoke tests, and deployment validation

ZTAM is not yet fully enterprise-grade because it still lacks some of the operating model that large companies expect:

- stronger CI/CD policy gates
- formal app integration standards
- audit-focused change management
- observability standards
- production deployment profiles beyond local compose
- control-plane style admin workflows

## Phase 1: Enterprise Integration Contract

Objective: make it explicit what an application must support before it can be protected by ZTAM.

Target outcomes:

- Define supported application assumptions.
- Define what a client may need to change.
- Define supported login integration patterns.
- Remove the false expectation that any URL can be protected automatically.

Deliverables:

- `INTEGRATION_CONTRACT.md`
- updated customer/operator guidance

Status: `in-progress`

## Phase 2: Enterprise Validation Gates

Objective: move key checks from operator habit into CI enforcement.

Target outcomes:

- Validate tenant configs automatically.
- Validate Python script syntax automatically.
- Validate shell script syntax automatically.
- Validate Docker Compose config automatically.
- Keep OPA tests and image builds as mandatory checks.

Deliverables:

- expanded GitHub Actions workflow

Status: `in-progress`

## Phase 3: Governance And Auditability

Objective: make tenant and policy change handling more accountable.

Target outcomes:

- versioned tenant changes with review gates
- clear promotion path from test to production
- documented rollback workflow
- explicit ownership of tenant onboarding and approval

Deliverables:

- tenant change policy
- release workflow documentation
- rollback procedure

Status: `in-progress`

## Phase 4: Observability And Incident Readiness

Objective: operate ZTAM like a platform service, not just a gateway stack.

Target outcomes:

- structured runtime logs
- policy decision visibility
- metrics for auth failures, denies, and upstream errors
- dashboard and alert requirements
- incident response runbook

Deliverables:

- observability architecture doc
- logging and metrics plan
- incident/runbook docs
- local observability profile for platform review

Status: `in-progress`

## Phase 5: Production Platform Hardening

Objective: align runtime deployment with enterprise production practices.

Target outcomes:

- externalized secrets management
- production-grade Keycloak and database guidance
- HA-ready deployment model
- environment separation
- managed certificate and DNS process

Deliverables:

- production reference architecture
- secrets-management guidance
- environment topology doc

Status: `next`

## Phase 6: Control Plane Maturity

Objective: separate administrative workflows from runtime enforcement.

Target outcomes:

- admin API or UI for tenant lifecycle
- approval workflow for onboarding and permission changes
- change history for tenant definitions
- self-service experience for platform operators

Deliverables:

- control-plane design
- API/UI backlog

Status: `later`

## Immediate Execution Slice

The current slice being implemented inside this repository is:

1. Add structured logs and request correlation in the auth runtime.
2. Add a metrics endpoint plus a local monitoring profile for review.
3. Keep the governance and incident-readiness docs aligned with real implementation.

These changes still do not make ZTAM fully enterprise-grade by themselves, but they move the repo closer to a big-company operating model instead of stopping at architecture alone.
