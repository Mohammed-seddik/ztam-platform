package authz

import future.keywords.if
import future.keywords.in

# ─── Top-level decision ──────────────────────────────────────────────────────
default allow := false
default deny_reason := "access denied by default policy"

allow if {
    role_permitted
    device_ok
}

# ─── Deny reason ─────────────────────────────────────────────────────────────
deny_reason := "insufficient device trust score" if {
    not device_ok
    role_permitted
}

deny_reason := "role does not have permission for this resource" if {
    not role_permitted
}

# ─── Role-based access control ───────────────────────────────────────────────
# admin always has full access
role_permitted if {
    "admin" in input.user.roles
}

# non-admin roles: look up permissions from data file
role_permitted if {
    some role in input.user.roles
    role != "admin"
    perms := data.permissions.roles[role]
    path_matches_prefix(input.request.path, perms.allowed_paths)
    input.request.method in perms.allowed_methods
    not path_matches_prefix(input.request.path, perms.denied_paths)
}

# ─── Device trust rules ──────────────────────────────────────────────────────
device_ok if {
    is_admin_path
    input.device.score >= 80
    input.device.encrypted == true
}

device_ok if {
    not is_admin_path
    input.device.score >= 60
}

is_admin_path if {
    startswith(input.request.path, "/admin/")
}

is_admin_path if {
    input.request.path == "/admin"
}

# ─── Helper ──────────────────────────────────────────────────────────────────
path_matches_prefix(path, prefixes) if {
    some prefix in prefixes
    startswith(path, prefix)
}
