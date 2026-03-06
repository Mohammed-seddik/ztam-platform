package authz_test

import future.keywords.if

import data.authz

# Inline permissions so tests pass without the JSON file on disk.
# Use `with data.permissions as _perms` (not `with data as ...`) to avoid OPA
# recursion-detection false positives when the test constant lives in data.authz_test.
_perms := {
    "roles": {
        "user": {
            "allowed_paths":   ["/api/"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET", "POST", "DELETE", "PUT", "PATCH"]
        },
        "editor": {
            "allowed_paths":   ["/api/"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET", "POST", "DELETE", "PUT", "PATCH"]
        },
        "viewer": {
            "allowed_paths":   ["/api/"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET"]
        }
    }
}

# ─── admin ────────────────────────────────────────────────────────────────────

test_admin_allows_any_method if {
    authz.allow
      with input as {"user": {"id": "u1", "roles": ["admin"]},
                     "request": {"method": "DELETE", "path": "/admin/users"}}
      with data.permissions as _perms
}

test_admin_allows_api if {
    authz.allow
      with input as {"user": {"id": "u1", "roles": ["admin"]},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

# ─── user role ────────────────────────────────────────────────────────────────

test_user_get_api if {
    authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_user_post_api if {
    authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "POST", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_user_delete_own_task if {
    authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "DELETE", "path": "/api/tasks/5"}}
      with data.permissions as _perms
}

test_user_denied_admin_delete if {
    not authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "DELETE", "path": "/admin/users"}}
      with data.permissions as _perms
}

test_user_denied_admin_path if {
    not authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/admin/users"}}
      with data.permissions as _perms
}

# ─── viewer role ──────────────────────────────────────────────────────────────

test_viewer_get_api if {
    authz.allow
      with input as {"user": {"id": "u3", "roles": ["viewer"]},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_viewer_denied_post if {
    not authz.allow
      with input as {"user": {"id": "u3", "roles": ["viewer"]},
                     "request": {"method": "POST", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_viewer_deny_reason if {
    authz.deny_reason == "role does not have permission for this resource"
      with input as {"user": {"id": "u3", "roles": ["viewer"]},
                     "request": {"method": "POST", "path": "/api/tasks"}}
      with data.permissions as _perms
}

# ─── empty user id ────────────────────────────────────────────────────────────

test_empty_id_denied if {
    not authz.allow
      with input as {"user": {"id": "", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_empty_id_deny_reason if {
    authz.deny_reason == "missing user identity"
      with input as {"user": {"id": "", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

# ─── no roles ─────────────────────────────────────────────────────────────────

test_no_roles_denied if {
    not authz.allow
      with input as {"user": {"id": "u4", "roles": []},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}

test_no_roles_deny_reason if {
    authz.deny_reason == "no roles assigned to user"
      with input as {"user": {"id": "u4", "roles": []},
                     "request": {"method": "GET", "path": "/api/tasks"}}
      with data.permissions as _perms
}
