package authz

import future.keywords.if
import future.keywords.in

# ── Top-level decision ──────────────────────────────────────────────────────
default allow        := false
default deny_reason  := "access denied by default policy"

allow if {
    input.user.id != ""
    count(input.user.roles) > 0
    role_permitted
    # device_ok is intentionally excluded from Phase 1.
    # Phase 2 will add real device trust store integration.
}

# ── Deny reason (else-chain prevents OPA undefined/conflict errors) ─────────
deny_reason := reason if {
    input.user.id == ""
    reason := "missing user identity"
} else := reason if {
    count(input.user.roles) == 0
    reason := "no roles assigned to user"
} else := reason if {
    not role_permitted
    reason := "role does not have permission for this resource"
}

# ── Role-based access control ───────────────────────────────────────────────
# admin always has full access to everything
role_permitted if {
    "admin" in input.user.roles
}

# non-admin roles: look up tenant-specific permissions, fall back to global.
# object.get handles the case where tenant_id is missing from input.user.
role_permitted if {
    some role in input.user.roles
    role != "admin"
    tenant_id := object.get(input.user, "tenant_id", "")
    perms     := _resolve_perms(tenant_id, role)
    path_matches_prefix(input.request.path, perms.allowed_paths)
    input.request.method in perms.allowed_methods
    not path_matches_prefix(input.request.path, perms.denied_paths)
}

# ── Tenant-aware permission lookup ──────────────────────────────────────────
# 1st priority: policies/tenants.json entry for this tenant_id
# Fallback:     policies/permissions.json (global default)
_resolve_perms(tenant_id, role) = p if {
    tenant_id != ""
    p := data.tenants[tenant_id].roles[role]
}

_resolve_perms(tenant_id, role) = p if {
    not _has_tenant_role(tenant_id, role)
    p := data.permissions.roles[role]
}

_has_tenant_role(tenant_id, role) if {
    tenant_id != ""
    data.tenants[tenant_id].roles[role]
}

# ── Helper ───────────────────────────────────────────────────────────────────
path_matches_prefix(path, prefixes) if {
    some prefix in prefixes
    startswith(path, prefix)
}
