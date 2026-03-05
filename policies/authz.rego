package authz

import future.keywords.if
import future.keywords.in

# ─── Top-level decision ──────────────────────────────────────────────────────
default allow        := false
default deny_reason  := "access denied by default policy"

allow if {
    input.user.id != ""
    count(input.user.roles) > 0
    role_permitted
    # device_ok is intentionally excluded from Phase 1.
    # Phase 2 will add real device trust store integration.
}

# ─── Deny reason (else-chain prevents OPA undefined/conflict errors) ─────────
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

# ─── Role-based access control ───────────────────────────────────────────────
# admin always has full access to everything
role_permitted if {
    "admin" in input.user.roles
}

# non-admin roles: look up permissions from data file (permissions.json)
role_permitted if {
    some role in input.user.roles
    role != "admin"
    perms := data.permissions.roles[role]
    path_matches_prefix(input.request.path, perms.allowed_paths)
    input.request.method in perms.allowed_methods
    not path_matches_prefix(input.request.path, perms.denied_paths)
}

# ─── Helper ──────────────────────────────────────────────────────────────────
path_matches_prefix(path, prefixes) if {
    some prefix in prefixes
    startswith(path, prefix)
}
