#!/usr/bin/env python3
"""
Add a new tenant entry to policies/tenants.json.
Called by onboard-tenant.sh — do not run directly unless you know what you're doing.
"""
import argparse
import json
import sys
import os


def default_perms(role: str) -> dict | None:
    """Return a sensible default permission set for a given role name."""
    role_lower = role.lower()
    if role_lower == "admin":
        return None  # admin is handled by OPA hardcoded rule (full access)
    if role_lower in ("manager", "editor", "moderator"):
        return {
            "allowed_paths":   ["/api/"],
            "denied_paths":    ["/admin/"],
            "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]
        }
    if role_lower == "viewer":
        return {
            "allowed_paths":   ["/api/"],
            "denied_paths":    ["/admin/", "/admin"],
            "allowed_methods": ["GET"]
        }
    # Default: user / employee / member — read + create, no admin
    return {
        "allowed_paths":   ["/api/"],
        "denied_paths":    ["/admin/", "/admin"],
        "allowed_methods": ["GET", "POST"]
    }


def main():
    p = argparse.ArgumentParser(description="Add tenant to policies/tenants.json")
    p.add_argument("--name",     required=True, help="Tenant name (= Keycloak client ID)")
    p.add_argument("--roles",    required=True, help="Comma-separated role list")
    p.add_argument("--tenants",  required=True, help="Path to policies/tenants.json")
    args = p.parse_args()

    # Load existing tenants.json
    with open(args.tenants, "r", encoding="utf-8") as f:
        tenants = json.load(f)

    if args.name in tenants:
        print(f"   ⚠  Tenant '{args.name}' already exists in tenants.json — not overwriting")
        print(f"      Edit policies/tenants.json manually to update permissions.")
        return

    # Build roles map
    roles_map = {}
    for role in [r.strip() for r in args.roles.split(",") if r.strip()]:
        perms = default_perms(role)
        if perms is not None:
            roles_map[role] = perms

    tenants[args.name] = {"roles": roles_map}

    with open(args.tenants, "w", encoding="utf-8") as f:
        json.dump(tenants, f, indent=2)
        f.write("\n")

    print(f"   ✓ Added '{args.name}' to tenants.json with roles: {list(roles_map.keys())}")


if __name__ == "__main__":
    main()
