"""
ZTAM Auth Middleware — Keycloak 26 + Envoy ext_authz + OPA
Real JWT validation (RS256 via JWKS), real OPA call, real token translation.
"""

import asyncio
import json
import os
import time
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response
from jose import JWTError, jwt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("auth-middleware")

app = FastAPI(title="ZTAM Auth Middleware")

# ─── Config from environment ─────────────────────────────────────────────────
KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KC_REALM: str = os.getenv("KC_REALM", "test-tenant")
KC_CLIENT_ID: str = os.getenv("KC_CLIENT_ID", "test-app")
KC_CLIENT_SECRET: str = os.getenv("KC_CLIENT_SECRET", "")
OPA_URL: str = os.getenv("OPA_URL", "http://opa:8181")
TESTAPP_JWT_SECRET: Optional[str] = os.getenv("TESTAPP_JWT_SECRET")

JWKS_URI: str = f"{KEYCLOAK_URL}/realms/{KC_REALM}/protocol/openid-connect/certs"
KC_TOKEN_URI: str = f"{KEYCLOAK_URL}/realms/{KC_REALM}/protocol/openid-connect/token"
EXPECTED_ISSUER: str = f"{KEYCLOAK_URL}/realms/{KC_REALM}"

# ─── JWKS in-memory cache with asyncio lock (prevents thundering herd) ───────
_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()
JWKS_TTL: int = 300  # seconds

# Keycloak internal roles that should never reach OPA or downstream headers
_KC_INTERNAL_ROLES = frozenset({
    "offline_access",
    "uma_authorization",
    f"default-roles-{KC_REALM}",
})


async def get_jwks() -> dict:
    """Fetch and cache Keycloak JWKS. Thread-safe under concurrent async requests."""
    global _jwks_cache, _jwks_fetched_at

    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at) < JWKS_TTL:
        return _jwks_cache

    async with _jwks_lock:
        # Double-check after acquiring the lock
        now = time.time()
        if _jwks_cache and (now - _jwks_fetched_at) < JWKS_TTL:
            return _jwks_cache

        logger.info("Fetching JWKS from %s", JWKS_URI)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(JWKS_URI)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = time.time()
            return _jwks_cache


def extract_roles(claims: dict) -> list[str]:
    """
    Extract meaningful roles from Keycloak JWT claims.
    Filters out Keycloak-internal roles (offline_access, uma_authorization, etc.)
    so only application roles (admin, user, viewer) reach OPA and downstream.
    """
    roles: list[str] = []

    # Realm-level roles
    realm_access = claims.get("realm_access", {})
    roles.extend(realm_access.get("roles", []))

    # Client-level roles
    resource_access = claims.get("resource_access", {})
    for client_roles in resource_access.values():
        roles.extend(client_roles.get("roles", []))

    # Custom role attribute set by the Java SPI (most important for ZTAM)
    custom_role = claims.get("role") or claims.get("user_role")
    if isinstance(custom_role, str) and custom_role:
        roles.append(custom_role)
    elif isinstance(custom_role, list):
        roles.extend(custom_role)

    # Deduplicate, remove empty strings, and strip Keycloak internal roles
    return [r for r in dict.fromkeys(roles) if r and r not in _KC_INTERNAL_ROLES]


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/login-proxy")
async def login_proxy(request: Request):
    """
    Intercepts POST /api/auth/login (routed here by Envoy).
    Delegates authentication to Keycloak (which uses the SPI to read from
    TestApp's MySQL DB), then returns a TestApp-compatible response.
    The browser stores the RS256 Keycloak token; subsequent requests through
    Envoy are validated by the check() handler below.
    """
    try:
        body = await request.json()
    except Exception:
        return Response(content='{"error":"invalid request body"}',
                        status_code=400, media_type="application/json")

    username: str = body.get("username", "").strip()
    password: str = body.get("password", "")

    if not username or not password:
        return Response(
            content='{"error":"username and password are required."}',
            status_code=400, media_type="application/json"
        )

    # ── 1. Authenticate via Keycloak token endpoint ───────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            kc_resp = await client.post(
                KC_TOKEN_URI,
                data={
                    "grant_type":    "password",
                    "client_id":     KC_CLIENT_ID,
                    "client_secret": KC_CLIENT_SECRET,
                    "username":      username,
                    "password":      password,
                },
            )
    except Exception as exc:
        logger.error("Keycloak unreachable: %s", exc)
        return Response(content='{"error":"auth service unavailable"}',
                        status_code=503, media_type="application/json")

    if kc_resp.status_code != 200:
        logger.warning("Keycloak rejected login for %s: %s", username, kc_resp.text)
        return Response(content='{"error":"Invalid credentials."}',
                        status_code=401, media_type="application/json")

    kc_data = kc_resp.json()
    kc_token: str = kc_data["access_token"]

    # ── 2. Decode claims safely using python-jose (handles padding, structure) ─
    try:
        claims = jwt.get_unverified_claims(kc_token)
    except JWTError as exc:
        logger.error("Failed to decode Keycloak token: %s", exc)
        return Response(content='{"error":"malformed token from IdP"}',
                        status_code=500, media_type="application/json")

    role: str = claims.get("role") or "viewer"
    uname: str = claims.get("preferred_username", username)

    logger.info("[login-proxy] Keycloak authenticated %s (role=%s)", uname, role)

    # ── 3. Return RS256 Keycloak token to the browser ─────────────────────────
    # The browser stores this and sends it on every subsequent API request.
    # The check() handler below will validate it via JWKS and translate it to
    # an HS256 token before forwarding to TestApp.
    return Response(
        content=json.dumps({"token": kc_token, "username": uname, "role": role}),
        status_code=200,
        media_type="application/json",
    )


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def check(request: Request, full_path: str = ""):
    """
    Envoy ext_authz handler. Called for every request that requires auth.
    Returns HTTP 200 (allow) with downstream headers, or 401/403 (deny).
    """
    # ---------- 0. CORS preflight passthrough --------------------------------
    # Browser OPTIONS preflight requests never carry Authorization headers.
    # Return 200 immediately so CORS works without exposing auth logic.
    if request.method == "OPTIONS":
        return Response(status_code=200)

    # ---------- 1. Extract Bearer token --------------------------------------
    auth_header: str = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        logger.warning("Missing or non-Bearer Authorization header")
        return Response(
            content='{"error":"missing token"}',
            status_code=401,
            media_type="application/json",
        )

    token = auth_header[7:]  # always 7 chars: "bearer "

    # ---------- 2 & 3. Fetch JWKS (cached, lock-protected) ------------------
    try:
        jwks = await get_jwks()
    except Exception as exc:
        logger.error("Failed to fetch JWKS: %s", exc)
        return Response(
            content='{"error":"auth service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    # ---------- 4 & 5. Validate RS256 JWT (signature + expiry + aud + iss) --
    try:
        claims: dict = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=KC_CLIENT_ID,   # validates aud == KC_CLIENT_ID
            issuer=EXPECTED_ISSUER,  # validates iss == http://keycloak:8080/realms/{realm}
        )
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        return Response(
            content='{"error":"invalid or expired token"}',
            status_code=403,
            media_type="application/json",
        )

    # ---------- 6. Extract user claims ---------------------------------------
    user_id: str     = claims.get("sub", "")
    email: str       = claims.get("email", "")
    tenant_id: str   = claims.get("tenant_id") or claims.get("azp") or KC_REALM
    roles: list[str] = extract_roles(claims)

    if not user_id:
        return Response(
            content='{"error":"invalid token: missing sub"}',
            status_code=403,
            media_type="application/json",
        )

    if not roles:
        roles = ["viewer"]

    # ---------- 7. Build OPA input -------------------------------------------
    original_path: str   = "/" + full_path if full_path else str(request.url.path)
    original_method: str = request.method.upper()

    opa_input = {
        "input": {
            "user": {
                "id":        user_id,
                "email":     email,
                "roles":     roles,
                "tenant_id": tenant_id,
            },
            "request": {
                "path":   original_path,
                "method": original_method,
            },
        }
    }

    # ---------- 8. Single OPA call — get allow + deny_reason together --------
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            opa_resp = await client.post(
                f"{OPA_URL}/v1/data/authz",
                json=opa_input,
            )
            opa_resp.raise_for_status()
            opa_result: dict = opa_resp.json().get("result", {})
    except Exception as exc:
        logger.error("OPA call failed: %s", exc)
        return Response(
            content='{"error":"policy engine unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    allowed: bool    = bool(opa_result.get("allow", False))
    deny_reason: str = opa_result.get("deny_reason", "access denied by default policy")

    # ---------- 9. Allow: translate token and set upstream headers -----------
    if allowed:
        username: str    = claims.get("preferred_username", user_id)
        custom_role: str = claims.get("role") or claims.get("user_role") or ""
        primary_role: str = custom_role if custom_role else (roles[0] if roles else "viewer")

        extra_headers: dict = {}
        if TESTAPP_JWT_SECRET:
            now_ts = int(time.time())
            db_user_id = claims.get("db_user_id") or user_id
            downstream_token = jwt.encode(
                {
                    "sub":      db_user_id,
                    "username": username,
                    "role":     primary_role,
                    "iat":      now_ts,
                    "exp":      now_ts + 3600,
                },
                TESTAPP_JWT_SECRET,
                algorithm="HS256",
            )
            extra_headers["authorization"] = f"Bearer {downstream_token}"
            logger.info(
                "Access allowed — user=%s role=%s path=%s → HS256 token issued",
                username, primary_role, original_path,
            )

        # Only send clean application roles in the header (no Keycloak internals)
        clean_roles = [r for r in roles if r not in _KC_INTERNAL_ROLES]
        return Response(
            status_code=200,
            headers={
                "x-user-id":    user_id,
                "x-user-roles": ",".join(clean_roles),
                "x-tenant-id":  tenant_id,
                **extra_headers,
            },
        )

    # ---------- 10. Deny ----------------------------------------------------
    logger.info(
        "Access denied — user=%s roles=%s path=%s reason=%s",
        user_id, roles, original_path, deny_reason,
    )
    return Response(
        content=json.dumps({"error": "access denied", "reason": deny_reason}),
        status_code=403,
        media_type="application/json",
    )
