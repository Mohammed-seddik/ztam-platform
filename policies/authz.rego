package authz

import future.keywords.if
import future.keywords.in

default allow := false
default deny_reason := "access denied by default policy"

subject := object.get(input, "subject", object.get(input, "user", {}))
tenant := object.get(input, "tenant", {"id": object.get(subject, "tenant_id", object.get(object.get(input, "user", {}), "tenant_id", ""))})
request_ctx := object.get(input, "request", {})

allow if {
    subject_id != ""
    count(subject_roles) > 0
    role_permitted
}

deny_reason := reason if {
    subject_id == ""
    reason := "missing user identity"
} else := reason if {
    count(subject_roles) == 0
    reason := "no roles assigned to user"
} else := reason if {
    not role_permitted
    reason := "role does not have permission for this resource"
}

subject_id := object.get(subject, "id", "")
subject_roles := object.get(subject, "roles", [])
tenant_id := object.get(tenant, "id", object.get(object.get(input, "user", {}), "tenant_id", ""))
request_path := object.get(request_ctx, "path", "")
request_method := object.get(request_ctx, "method", "")

role_permitted if {
    "admin" in subject_roles
}

role_permitted if {
    some role in subject_roles
    role != "admin"
    perms := _resolve_perms(tenant_id, role)
    path_allowed(request_path, perms)
    request_method in perms.allowed_methods
    not path_denied(request_path, perms)
}

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

path_matches_prefix(path, prefixes) if {
    some prefix in prefixes
    startswith(path, prefix)
}

path_matches_exact(path, exact_paths) if {
    some exact_path in exact_paths
    path == exact_path
}

path_allowed(path, perms) if {
    path_matches_prefix(path, object.get(perms, "allowed_paths", []))
}

path_allowed(path, perms) if {
    path_matches_exact(path, object.get(perms, "allowed_exact_paths", []))
}

path_denied(path, perms) if {
    path_matches_prefix(path, object.get(perms, "denied_paths", []))
}

path_denied(path, perms) if {
    path_matches_exact(path, object.get(perms, "denied_exact_paths", []))
}
