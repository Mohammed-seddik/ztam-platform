"""
ZTAM Auth Middleware
Envoy ext_authz HTTP service — validates JWT via Keycloak JWKS, then calls OPA.
"""

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
# TestApp's JWT_SECRET — used to mint a downstream HS256 token that TestApp accepts
TESTAPP_JWT_SECRET: Optional[str] = os.getenv("TESTAPP_JWT_SECRET")

JWKS_URI: str = f"{KEYCLOAK_URL}/realms/{KC_REALM}/protocol/openid-connect/certs"
KC_TOKEN_URI: str = f"{KEYCLOAK_URL}/realms/{KC_REALM}/protocol/openid-connect/token"

# ─── JWKS in-memory cache ────────────────────────────────────────────────────
_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0
JWKS_TTL: int = 300  # seconds


async def get_jwks() -> dict:
    """Fetch and cache Keycloak's JWKS. Refreshes every JWKS_TTL seconds."""
    global _jwks_cache, _jwks_fetched_at

    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at) < JWKS_TTL:
        return _jwks_cache

    logger.info("Fetching JWKS from %s", JWKS_URI)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(JWKS_URI)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        return _jwks_cache


def extract_roles(claims: dict) -> list[str]:
    """
    Pull roles from the JWT. Keycloak stores client roles under
    resource_access.<client_id>.roles and realm roles under realm_access.roles.
    """
    roles: list[str] = []

    # Realm-level roles
    realm_access = claims.get("realm_access", {})
    roles.extend(realm_access.get("roles", []))

    # All client-level roles
    resource_access = claims.get("resource_access", {})
    for client_roles in resource_access.values():
        roles.extend(client_roles.get("roles", []))

    # Custom role attribute set by our Java SPI
    custom_role = claims.get("role") or claims.get("user_role")
    if isinstance(custom_role, str) and custom_role:
        roles.append(custom_role)
    elif isinstance(custom_role, list):
        roles.extend(custom_role)

    # Deduplicate and remove empty strings
    return list({r for r in roles if r})


def get_device_context(device_id: Optional[str]) -> dict:
    """
    Build a device context object.
    In production this would query a device trust store.
    For now: known device IDs get score 90, unknowns get 70.
    """
    default_score = 70
    encrypted = True  # assume encrypted by default

    if device_id:
        # A real implementation would look up the device posture DB.
        # Placeholder: any non-empty device ID is treated as known → score 90.
        default_score = 90

    return {
        "id": device_id or "unknown",
        "score": default_score,
        "encrypted": encrypted,
    }


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/login-proxy")
async def login_proxy(request: Request):
    """
    Intercepts POST /api/auth/login from the TestApp frontend (via Envoy).
    Authenticates the user against Keycloak (which uses the SPI to read
    from TestApp's MySQL DB), then returns a TestApp-compatible HS256 token
    so the frontend works exactly as before — without touching the app.
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

    # ── 1. Authenticate via Keycloak (SPI reads testapp MySQL) ──────────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            kc_resp = await client.post(
                KC_TOKEN_URI,
                data={
                    "grant_type": "password",
                    "client_id": KC_CLIENT_ID,
                    "client_secret": KC_CLIENT_SECRET,
                    "username": username,
                    "password": password,
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

    # ── 2. Decode Keycloak claims (no need to re-verify — we just called KC) ─
    import base64 as _b64, json as _json
    raw = kc_token.split(".")[1]
    claims = _json.loads(_b64.urlsafe_b64decode(raw + "=="))

    db_user_id = claims.get("db_user_id") or claims.get("sub")
    role: str = claims.get("role") or "user"
    uname: str = claims.get("preferred_username", username)

    logger.info("[login-proxy] Keycloak authenticated %s (role=%s, db_id=%s)",
                uname, role, db_user_id)

    # ── 3. Mint HS256 token TestApp frontend understands ─────────────────────
    if not TESTAPP_JWT_SECRET:
        return Response(content='{"error":"server misconfiguration"}',
                        status_code=500, media_type="application/json")

    now_ts = int(time.time())
    downstream_token: str = jwt.encode(
        {"sub": db_user_id, "username": uname, "role": role,
         "iat": now_ts, "exp": now_ts + 3600},
        TESTAPP_JWT_SECRET,
        algorithm="HS256",
    )

    # ── 4. Return same shape as TestApp's own /api/auth/login ────────────────
    # IMPORTANT: return the Keycloak RS256 token (not HS256) so subsequent
    # API calls through Envoy are validated by auth-middleware via JWKS.
    # auth-middleware will translate to HS256 before forwarding to TestApp.
    import json as _j
    return Response(
        content=_j.dumps({"token": kc_token, "username": uname, "role": role}),
        status_code=200,
        media_type="application/json",
    )


# Catch-all: Envoy HTTP ext_authz sends the original request method+path
# to the auth service (e.g. GET /api/me, POST /api/data).
# We handle every method on every path.
@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def check(request: Request, full_path: str = ""):
    """
    Called by Envoy ext_authz on every incoming request.
    Returns 200 (allow) or 403/401 (deny).
    """
    # ---------- 1. Extract the JWT bearer token ----------
    auth_header: str = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        logger.warning("Missing or non-Bearer Authorization header")
        return Response(
            content='{"error":"missing token"}',
            status_code=401,
            media_type="application/json",
        )

    token = auth_header[len("bearer "):]

    # ---------- 2 & 3. Fetch JWKS (cached) ----------
    try:
        jwks = await get_jwks()
    except Exception as exc:
        logger.error("Failed to fetch JWKS: %s", exc)
        return Response(
            content='{"error":"auth service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    # ---------- 4 & 5. Validate JWT ----------
    try:
        claims: dict = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},  # audience check done by OPA/app
        )
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        return Response(
            content='{"error":"invalid or expired token"}',
            status_code=403,
            media_type="application/json",
        )

    # ---------- 6. Extract user claims ----------
    user_id: str = claims.get("sub", "")
    email: str = claims.get("email", "")
    tenant_id: str = claims.get("tenant_id") or claims.get("azp") or KC_REALM
    roles: list[str] = extract_roles(claims)

    if not roles:
        roles = ["viewer"]

    # ---------- 7. Build device context ----------
    device_id: Optional[str] = request.headers.get("x-device-id")
    device = get_device_context(device_id)

    # ---------- 8. Call OPA ----------
    # Envoy forwards the original request path and method directly.
    original_path: str = "/" + full_path if full_path else str(request.url.path)
    original_method: str = request.method

    opa_input = {
        "input": {
            "user": {
                "id": user_id,
                "email": email,
                "roles": roles,
                "tenant_id": tenant_id,
            },
            "request": {
                "path": original_path,
                "method": original_method.upper(),
            },
            "device": device,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            opa_resp = await client.post(
                f"{OPA_URL}/v1/data/authz/allow",
                json=opa_input,
            )
            opa_resp.raise_for_status()
            opa_body: dict = opa_resp.json()
    except Exception as exc:
        logger.error("OPA call failed: %s", exc)
        return Response(
            content='{"error":"policy engine unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    allowed: bool = opa_body.get("result", False)

    # ---------- 9. Allow ----------
    if allowed:
        username: str = claims.get("preferred_username", user_id)
        # Prefer the custom 'role' claim set by the SPI (admin/user/editor/viewer)
        # over Keycloak's internal realm roles (default-roles-*, offline_access, etc.)
        custom_role: str = claims.get("role") or claims.get("user_role") or ""
        primary_role: str = custom_role if custom_role else (roles[0] if roles else "viewer")

        # ── Token translation ──────────────────────────────────────────────
        # When TESTAPP_JWT_SECRET is set, mint an HS256 token that the
        # downstream app (TestApp) can validate with its own JWT middleware.
        # This avoids touching TestApp while still enforcing Keycloak auth.
        extra_headers: dict = {}
        if TESTAPP_JWT_SECRET:
            now_ts = int(time.time())
            # db_user_id may be set by the SPI as a custom claim
            db_user_id = claims.get("db_user_id") or claims.get("sub")
            downstream_payload = {
                "sub": db_user_id,
                "username": username,
                "role": primary_role,
                "iat": now_ts,
                "exp": now_ts + 3600,  # 1 hour
            }
            downstream_token = jwt.encode(
                downstream_payload,
                TESTAPP_JWT_SECRET,
                algorithm="HS256",
            )
            extra_headers["authorization"] = f"Bearer {downstream_token}"
            logger.info(
                "Token translated for %s (role=%s) → HS256 downstream token issued",
                username, primary_role,
            )

        return Response(
            status_code=200,
            headers={
                "x-user-id": user_id,
                "x-user-roles": ",".join(roles),
                "x-tenant-id": tenant_id,
                **extra_headers,
            },
        )

    # ---------- 10. Deny: fetch reason from OPA ----------
    deny_reason = "access denied"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            reason_resp = await client.post(
                f"{OPA_URL}/v1/data/authz/deny_reason",
                json=opa_input,
            )
            if reason_resp.status_code == 200:
                reason_body = reason_resp.json()
                deny_reason = reason_body.get("result", deny_reason)
    except Exception:
        pass  # non-fatal, we already have a default reason

    logger.info(
        "Access denied — user=%s roles=%s path=%s reason=%s",
        user_id, roles, original_path, deny_reason,
    )

    import json as _json
    return Response(
        content=_json.dumps({"error": "access denied", "reason": deny_reason}),
        status_code=403,
        media_type="application/json",
    )
