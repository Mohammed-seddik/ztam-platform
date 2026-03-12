#!/usr/bin/env python3
"""Acceptance smoke test for a ZTAM-protected tenant."""

from __future__ import annotations

import argparse
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: str


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(insecure: bool, *, follow_redirects: bool = True) -> request.OpenerDirector:
    handlers: list[Any] = []
    if not follow_redirects:
        handlers.append(NoRedirectHandler())
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(request.HTTPSHandler(context=context))
    return request.build_opener(*handlers)


def fetch(
    opener: request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> HttpResult:
    req = request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return HttpResult(
                status=resp.getcode(),
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=resp.read().decode("utf-8", errors="replace"),
            )
    except error.HTTPError as exc:
        return HttpResult(
            status=exc.code,
            headers={k.lower(): v for k, v in exc.headers.items()},
            body=exc.read().decode("utf-8", errors="replace"),
        )


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def join_url(base_url: str, path: str) -> str:
    base_url = base_url.rstrip("/")
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base_url + path


def load_tenant_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def keycloak_password_grant(
    opener: request.OpenerDirector,
    *,
    keycloak_url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    timeout: int,
) -> HttpResult:
    body = parse.urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password,
        }
    ).encode("utf-8")
    return fetch(
        opener,
        f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token",
        method="POST",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )


def bearer_request(
    opener: request.OpenerDirector,
    url: str,
    *,
    token: str,
    host_header: str | None,
    timeout: int,
    accept: str,
) -> HttpResult:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
    }
    if host_header:
        headers["Host"] = host_header
    return fetch(opener, url, headers=headers, timeout=timeout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test a ZTAM tenant")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--host-header", help="Optional Host header override for pre-DNS validation")
    parser.add_argument("--tenant-config", help="Path to tenants/<name>/config.json")
    parser.add_argument("--keycloak-url", default="http://localhost:8080")
    parser.add_argument("--protected-path", default="/")
    parser.add_argument("--login-mode", choices=["auto", "form", "keycloak"], default="auto")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--tenant-username")
    parser.add_argument("--tenant-password")
    parser.add_argument("--cross-tenant-username")
    parser.add_argument("--cross-tenant-password")
    parser.add_argument("--non-admin-username")
    parser.add_argument("--non-admin-password")
    parser.add_argument("--expect-text")
    parser.add_argument("--expect-status", type=int, default=200)
    parser.add_argument("--denied-path", default="/admin")
    parser.add_argument("--deny-expected-status", type=int, default=403)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--insecure", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tenant_config = load_tenant_config(args.tenant_config)
    opener = build_opener(args.insecure)
    protected_url = join_url(args.base_url, args.protected_path)
    browser_headers = {"Accept": "text/html"}
    if args.host_header:
        browser_headers["Host"] = args.host_header

    initial = fetch(build_opener(args.insecure, follow_redirects=False), protected_url, headers=browser_headers, timeout=args.timeout)
    location = initial.headers.get("location", "")

    login_mode = args.login_mode
    if login_mode == "auto":
        login_mode = "keycloak" if "/ztam/login-redirect" in location else "form"

    print(f"Base URL: {args.base_url}")
    print(f"Protected path: {args.protected_path}")
    print(f"Detected login mode: {login_mode}")

    if login_mode == "keycloak":
        ensure(initial.status in {302, 303}, f"expected redirect for keycloak mode, got HTTP {initial.status}")
        ensure("/ztam/login-redirect" in location, "expected redirect to /ztam/login-redirect")
        redirect_result = fetch(
            build_opener(args.insecure, follow_redirects=False),
            join_url(args.base_url, location),
            headers=browser_headers,
            timeout=args.timeout,
        )
        redirect_location = redirect_result.headers.get("location", "")
        ensure(redirect_result.status in {302, 303}, f"expected Keycloak redirect, got HTTP {redirect_result.status}")
        ensure("/protocol/openid-connect/auth" in redirect_location, "expected Keycloak authorization endpoint")
        print("Keycloak redirect verified")

        realm = str(tenant_config.get("keycloak_realm", "")).strip()
        client_id = str(tenant_config.get("keycloak_client_id", "")).strip()
        client_secret = str(tenant_config.get("keycloak_client_secret", "")).strip()
        ensure(realm and client_id and client_secret, "--tenant-config must include keycloak_realm, keycloak_client_id, and keycloak_client_secret")

        tenant_username = args.tenant_username or args.username
        tenant_password = args.tenant_password or args.password
        ensure(tenant_username and tenant_password, "tenant credentials are required for keycloak smoke testing")

        success = keycloak_password_grant(
            opener,
            keycloak_url=args.keycloak_url,
            realm=realm,
            client_id=client_id,
            client_secret=client_secret,
            username=tenant_username,
            password=tenant_password,
            timeout=args.timeout,
        )
        ensure(success.status == 200, f"expected tenant realm login success, got HTTP {success.status}: {success.body}")
        success_body = json.loads(success.body)
        access_token = str(success_body.get("access_token", ""))
        ensure(access_token, "tenant login did not return an access_token")
        print(f"Tenant realm login verified for {tenant_username}")

        page = bearer_request(
            opener,
            protected_url,
            token=access_token,
            host_header=args.host_header,
            timeout=max(args.timeout, 40),
            accept="text/html",
        )
        ensure(page.status == args.expect_status, f"expected HTTP {args.expect_status} on protected page, got HTTP {page.status}")
        if args.expect_text:
            ensure(args.expect_text in page.body, f"expected protected page to contain: {args.expect_text}")
        print(f"Protected route verified with tenant token: HTTP {page.status}")

        if args.cross_tenant_username and args.cross_tenant_password:
            failure = keycloak_password_grant(
                opener,
                keycloak_url=args.keycloak_url,
                realm=realm,
                client_id=client_id,
                client_secret=client_secret,
                username=args.cross_tenant_username,
                password=args.cross_tenant_password,
                timeout=args.timeout,
            )
            ensure(failure.status in {400, 401}, f"expected cross-tenant login failure, got HTTP {failure.status}")
            print(f"Cross-tenant login isolation verified: HTTP {failure.status}")

        non_admin_username = args.non_admin_username or tenant_username
        non_admin_password = args.non_admin_password or tenant_password
        denied_token = access_token
        if args.non_admin_username and args.non_admin_password:
            denied_login = keycloak_password_grant(
                opener,
                keycloak_url=args.keycloak_url,
                realm=realm,
                client_id=client_id,
                client_secret=client_secret,
                username=non_admin_username,
                password=non_admin_password,
                timeout=args.timeout,
            )
            ensure(denied_login.status == 200, f"expected non-admin login success, got HTTP {denied_login.status}")
            denied_token = json.loads(denied_login.body).get("access_token", "")
            ensure(denied_token, "non-admin login did not return an access_token")
        denied = bearer_request(
            opener,
            join_url(args.base_url, args.denied_path),
            token=denied_token,
            host_header=args.host_header,
            timeout=max(args.timeout, 40),
            accept="application/json",
        )
        ensure(
            denied.status == args.deny_expected_status,
            f"expected HTTP {args.deny_expected_status} on denied route, got HTTP {denied.status}",
        )
        print(f"Role denial verified on {args.denied_path} with HTTP {denied.status}")
        return 0

    ensure(initial.status in {200, 302}, f"expected login page or redirect for form mode, got HTTP {initial.status}")
    ensure("/ztam/login" in location or "ZTAM" in initial.body, "expected ZTAM form-login experience")

    spoof = fetch(
        build_opener(args.insecure, follow_redirects=False),
        protected_url,
        headers={
            "Accept": "text/html",
            **({"Host": args.host_header} if args.host_header else {}),
            "X-Username": "mallory",
            "X-User-Roles": "admin",
            "X-User-Id": "spoofed",
            "X-Tenant-Id": "spoofed",
        },
        timeout=args.timeout,
    )
    ensure(spoof.status in {302, 303, 401, 403}, f"unexpected response to spoofed headers: HTTP {spoof.status}")
    print(f"Spoofed-header test: HTTP {spoof.status}")

    ensure(args.username and args.password, "--username and --password are required for form mode")
    login_result = fetch(
        opener,
        join_url(args.base_url, "/ztam/login"),
        method="POST",
        data=json.dumps(
            {
                "username": args.username,
                "password": args.password,
                "next": args.protected_path,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Host": args.host_header} if args.host_header else {}),
        },
        timeout=args.timeout,
    )
    ensure(login_result.status == 200, f"expected successful login, got HTTP {login_result.status}")
    login_body = json.loads(login_result.body)
    ensure(login_body.get("redirect") == args.protected_path, "unexpected redirect target after login")
    print(f"Form login verified for user {login_body.get('username', args.username)}")

    page = fetch(opener, protected_url, headers=browser_headers, timeout=max(args.timeout, 40))
    ensure(page.status == args.expect_status, f"expected HTTP {args.expect_status} on protected page, got HTTP {page.status}")
    if args.expect_text:
        ensure(args.expect_text in page.body, f"expected protected page to contain: {args.expect_text}")
    print(f"Protected page verified with HTTP {page.status}")

    denied_url = join_url(args.base_url, args.denied_path)
    denied = fetch(
        opener,
        denied_url,
        headers={"Accept": "application/json", **({"Host": args.host_header} if args.host_header else {})},
        timeout=max(args.timeout, 40),
    )
    ensure(
        denied.status == args.deny_expected_status,
        f"expected HTTP {args.deny_expected_status} on denied route, got HTTP {denied.status}",
    )
    print(f"Role denial verified on {args.denied_path} with HTTP {denied.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
