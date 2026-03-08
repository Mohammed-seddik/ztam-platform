# Platform Layout

`platform/control-plane/` holds the new tenant source-of-truth service.

`platform/contracts/` defines the canonical v1 tenant and auth-context contracts.

`platform/published/` contains generated runtime bundles consumed by the data plane.

The current repo still keeps legacy tenant configs under `tenants/` for migration and backward compatibility, but the target operating model is:

- control-plane DB/API = source of truth
- published bundles = runtime inputs
- demo assets = sample integration only
