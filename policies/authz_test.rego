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
            "allowed_exact_paths": ["/", "/dashboard.html", "/favicon.ico"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET", "POST", "DELETE"]
        },
        "editor": {
            "allowed_paths":   ["/api/"],
            "allowed_exact_paths": ["/", "/dashboard.html", "/favicon.ico"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]
        },
        "viewer": {
            "allowed_paths":   ["/api/"],
            "allowed_exact_paths": ["/", "/dashboard.html", "/favicon.ico"],
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

test_user_get_dashboard_shell if {
    authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/dashboard.html"}}
      with data.permissions as _perms
}

test_user_get_root_shell if {
    authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "GET", "path": "/"}}
      with data.permissions as _perms
}

test_user_can_delete_task if {
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

# ─── editor vs user role differentiation ──────────────────────────────────────

test_editor_can_put if {
    authz.allow
      with input as {"user": {"id": "u6", "roles": ["editor"]},
                     "request": {"method": "PUT", "path": "/api/tasks/1"}}
      with data.permissions as _perms
}

test_editor_can_delete if {
    authz.allow
      with input as {"user": {"id": "u6", "roles": ["editor"]},
                     "request": {"method": "DELETE", "path": "/api/tasks/1"}}
      with data.permissions as _perms
}

test_user_cannot_put if {
    not authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "PUT", "path": "/api/tasks/1"}}
      with data.permissions as _perms
}

test_user_cannot_patch if {
    not authz.allow
      with input as {"user": {"id": "u2", "roles": ["user"]},
                     "request": {"method": "PATCH", "path": "/api/tasks/1"}}
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

# ── multi-tenant permissions ────────────────────────────────────────────────
# Mock tenants.json format: top-level key is the tenant_id (= Keycloak azp claim)
_tenant_perms := {
    "myapp": {
        "roles": {
            "manager": {
                "allowed_paths":   ["/api/"],
                "allowed_exact_paths": ["/", "/dashboard.html"],
                "denied_paths":    ["/admin/"],
                "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]
            }
        }
    }
}

test_tenant_specific_role_allowed if {
    authz.allow
      with input as {"user": {"id": "u5", "roles": ["manager"], "tenant_id": "myapp"},
                     "request": {"method": "GET", "path": "/api/reports"}}
      with data.tenants as _tenant_perms
      with data.permissions as _perms
}

test_tenant_specific_role_denied_admin_path if {
    not authz.allow
      with input as {"user": {"id": "u5", "roles": ["manager"], "tenant_id": "myapp"},
                     "request": {"method": "DELETE", "path": "/admin/users"}}
      with data.tenants as _tenant_perms
      with data.permissions as _perms
}

test_v1_auth_context_shape if {
    authz.allow
      with input as {
          "tenant": {"id": "myapp", "integration_mode": "managed_oidc", "identity_mode": "managed"},
          "subject": {"id": "u7", "roles": ["manager"], "email": "u7@example.com"},
          "request": {"method": "GET", "path": "/api/reports"},
          "client": {"type": "browser", "host": "myapp.example.com"},
          "device": {"posture": "unknown"}
      }
      with data.tenants as _tenant_perms
      with data.permissions as _perms
}
