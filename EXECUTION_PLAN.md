# ZTAM Execution Plan

This file tracks the push from working demo to market-ready platform.

## Goal

Make ZTAM feel like a product a real client can buy, integrate, validate, and operate without tribal knowledge.

## Status Legend

- `done`: already implemented and validated
- `in-progress`: currently being executed
- `next`: highest-priority remaining work
- `later`: useful but not blocking the first sellable version

## Phase 1: Tenant Model

- `done` Tenant config is the source of truth in `tenants/<name>/config.json`
- `done` Policy generation is centralized in `scripts/tenant_manager.py`
- `done` Envoy tenant routing is generated from tenant configs
- `done` Onboard/offboard scripts use generated policy and routing flows

## Phase 2: Customer Workflow

- `done` Operator playbook exists in `ONBOARDING_PLAYBOOK.md`
- `done` Integration guide reflects generated-config architecture
- `done` Customer handoff template exists in `CUSTOMER_HANDOFF_TEMPLATE.md`
- `done` Acceptance smoke testing exists in `scripts/smoke_test_tenant.py`

## Phase 3: Runtime Behavior

- `done` Browser requests redirect to the correct login experience
- `done` Header spoofing is blocked before ext_authz
- `done` Form-login flow is validated live for the store tenant
- `done` Keycloak-mode redirect path is implemented in auth-middleware

## Phase 4: Production Readiness

- `done` Add a deployment audit script that validates `.env`, secrets, URLs, and TLS certificate files
- `done` Add missing production-facing environment variables to `.env.example`
- `done` Wire deployment validation into `README.md`, `DEPLOYMENT.md`, and the operator playbook
- `done` Validate hosted-Keycloak mode with the smoke-test workflow against a real tenant setup

## Phase 5: Product Packaging

- `done` Clean repo structure so platform assets and demo assets are more clearly separated
- `done` Add a go-live checklist covering DNS, certificates, smoke test, and customer handoff
- `done` Add CI checks for tenant config validation, script syntax, and compose config
- `later` Add a dedicated sample tenant for hosted-Keycloak mode

## Phase 6: Enterprise Alignment

- `done` Define an explicit application integration contract instead of implying any URL can be protected with zero adaptation
- `done` Document the enterprise target state and the gap between the current platform and big-company operating models
- `done` Add governance, rollback, and release-control workflows for tenant and policy changes
- `done` Define observability and audit requirements for production operation
- `done` Add repository pull-request scaffolding for validation evidence, approval, and rollback notes
- `done` Add structured auth-middleware logs, request correlation, and a Prometheus-style metrics surface
- `done` Add an optional local observability profile with Prometheus and Grafana for review demos
- `later` Add a true control-plane API/UI for tenant lifecycle management

## Execution Notes

- Work should favor repeatable scripts over manual operator steps.
- Generated files are outputs; tenant configs are the editable inputs.
- No change is considered finished until there is a validation path in the repo.
- Pre-DNS tenant checks should use `scripts/smoke_test_tenant.py --host-header ...`.
- Bundled sample-app assets now live under `demo/`; platform code stays at the repo root.
- Enterprise-style delivery requires an integration contract, validation gates, governance, observability, and stronger production operating practices.
- Governance lives in `TENANT_CHANGE_POLICY.md`; monitoring and incident expectations live in `OBSERVABILITY_RUNBOOK.md`.
