#!/usr/bin/env python3
"""Validate production-facing ZTAM deployment inputs.

Checks the env file, key URL relationships, placeholder secrets, and TLS files.
This is an operator audit script, not a runtime service.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_KEYS = [
    "PG_PASS",
    "KC_ADMIN_PASS",
    "KC_REALM",
    "KC_CLIENT_ID",
    "KC_CLIENT_SECRET",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "TESTAPP_JWT_SECRET",
]

DEFAULTED_KEYS = {
    "KC_ADMIN_USER": "admin",
    "KC_HOSTNAME": "localhost",
    "KEYCLOAK_URL": "http://localhost:8080",
    "KC_ISSUER_URL": "http://localhost:8080",
    "ZTAM_PUBLIC_URL": "https://localhost",
    "AUTH_COOKIE_SECURE": "true",
    "AUTH_COOKIE_SAMESITE": "lax",
}

PLACEHOLDER_SNIPPETS = {
    "change_me",
    "localhost",
    "yourdomain.com",
    "example.com",
}


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(snippet in lowered for snippet in PLACEHOLDER_SNIPPETS)


def validate_url(name: str, value: str, *, require_https: bool) -> list[str]:
    issues: list[str] = []
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        issues.append(f"{name} must start with http:// or https://")
        return issues
    if not parsed.hostname:
        issues.append(f"{name} must include a hostname")
        return issues
    if require_https and parsed.scheme != "https":
        issues.append(f"{name} should use https in production")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ZTAM deployment inputs")
    parser.add_argument("--env-file", default=".env", help="Path to the env file to validate")
    parser.add_argument("--cert-dir", default="envoy/certs", help="Directory containing Envoy TLS certs")
    parser.add_argument("--production", action="store_true", help="Apply stricter production checks")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    cert_dir = Path(args.cert_dir)

    if not env_path.exists():
        print(f"FAIL: env file not found: {env_path}")
        return 1

    env = load_env_file(env_path)
    errors: list[str] = []
    warnings: list[str] = []

    effective_env = dict(DEFAULTED_KEYS)
    effective_env.update(env)

    for key in REQUIRED_KEYS:
        if not effective_env.get(key):
            errors.append(f"missing required key: {key}")

    for key, default_value in DEFAULTED_KEYS.items():
        if key not in env:
            warnings.append(f"{key} not set in {env_path}; using default value {default_value!r}")

    for key, value in effective_env.items():
        if key in {"KC_ADMIN_USER", "KC_REALM", "KC_CLIENT_ID", "MYSQL_DATABASE", "MYSQL_USER", "AUTH_COOKIE_SAMESITE"}:
            continue
        if value and is_placeholder(value):
            warnings.append(f"{key} still looks like a placeholder value")

    warnings.extend(validate_url("KEYCLOAK_URL", effective_env["KEYCLOAK_URL"], require_https=args.production))
    warnings.extend(validate_url("KC_ISSUER_URL", effective_env["KC_ISSUER_URL"], require_https=args.production))
    warnings.extend(validate_url("ZTAM_PUBLIC_URL", effective_env["ZTAM_PUBLIC_URL"], require_https=args.production))

    keycloak_url = effective_env.get("KEYCLOAK_URL", "")
    issuer_url = effective_env.get("KC_ISSUER_URL", "")
    ztam_public_url = effective_env.get("ZTAM_PUBLIC_URL", "")
    kc_hostname = effective_env.get("KC_HOSTNAME", "")

    if issuer_url and kc_hostname and kc_hostname not in issuer_url:
        warnings.append("KC_ISSUER_URL does not appear to match KC_HOSTNAME")
    if args.production and ztam_public_url.endswith("localhost"):
        errors.append("ZTAM_PUBLIC_URL still points to localhost in production mode")
    if args.production and issuer_url.endswith("localhost:8080"):
        errors.append("KC_ISSUER_URL still points to localhost in production mode")
    if args.production and keycloak_url.endswith("localhost:8080"):
        warnings.append("KEYCLOAK_URL still points to localhost; this is only correct if auth-middleware reaches Keycloak through a public hostname")

    jwt_secret = env.get("TESTAPP_JWT_SECRET", "")
    if jwt_secret and len(jwt_secret) < 32:
        errors.append("TESTAPP_JWT_SECRET should be at least 32 characters")

    cookie_secure = effective_env.get("AUTH_COOKIE_SECURE", "true").lower()
    if args.production and cookie_secure != "true":
        errors.append("AUTH_COOKIE_SECURE should be true in production")

    if args.production:
        for key in ("KC_HOSTNAME", "KEYCLOAK_URL", "KC_ISSUER_URL", "ZTAM_PUBLIC_URL"):
            if key not in env:
                errors.append(f"{key} should be explicitly set in production")

    cert_file = cert_dir / "server.crt"
    key_file = cert_dir / "server.key"
    if not cert_file.exists():
        errors.append(f"missing TLS certificate: {cert_file}")
    if not key_file.exists():
        errors.append(f"missing TLS private key: {key_file}")

    print("ZTAM deployment audit")
    print(f"Env file: {env_path}")
    print(f"Cert dir: {cert_dir}")
    print(f"Mode: {'production' if args.production else 'basic'}")

    if warnings:
        print("\nWarnings:")
        for item in warnings:
            print(f"- {item}")

    if errors:
        print("\nErrors:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("\nPASS: deployment inputs look consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())