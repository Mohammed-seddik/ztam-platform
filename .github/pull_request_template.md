## Summary

Describe the tenant, policy, routing, or platform change.

## Change Type

- [ ] Standard change
- [ ] Sensitive change
- [ ] Emergency change

## Tenant Impact

- Tenant name:
- Affected hostname:
- Login mode:
- Routes affected:
- Roles affected:

## Validation Evidence

- [ ] `python3 scripts/tenant_manager.py validate`
- [ ] `python3 scripts/tenant_manager.py sync-policies`
- [ ] `python3 scripts/tenant_manager.py sync-envoy` if routing changed
- [ ] Smoke test attached for affected path or role
- [ ] Deployment validation attached if production-bound

Evidence links or command output summary:

## Rollback Plan

Explain exactly how this change will be reverted if it fails.

## Observability Impact

- How will operators detect if this change breaks access?
- Which logs, metrics, or alerts should they check first?

## Approval Notes

- Platform reviewer:
- Application or tenant approver:
- If emergency, explain why normal review was bypassed:
