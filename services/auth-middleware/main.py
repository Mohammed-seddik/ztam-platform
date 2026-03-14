"""
ZTAM Auth Middleware — Keycloak 26 + Envoy ext_authz + OPA
Real JWT validation (RS256 via JWKS), real OPA call, real token translation.
"""

import asyncio
import collections
import hashlib
import json
import os
import re
import threading
import time
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from redis.asyncio import Redis
from fastapi import FastAPI, Request, Response
from jose import JWTError, jwt

LOG_FORMAT = os.getenv("LOG_FORMAT", "json").strip().lower() or "json"
ZTAM_ENVIRONMENT = os.getenv("ZTAM_ENVIRONMENT", os.getenv("ENVIRONMENT", "dev"))

_LOG_RESERVED_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": "auth-middleware",
            "environment": ZTAM_ENVIRONMENT,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _LOG_RESERVED_FIELDS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_log_handler = logging.StreamHandler()
if LOG_FORMAT == "json":
    _log_handler.setFormatter(JsonFormatter())
else:
    _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_log_handler], force=True)
logger = logging.getLogger("auth-middleware")

app = FastAPI(title="ZTAM Auth Middleware")


def _request_id(request: Optional[Request]) -> str:
    if request is None:
        return ""
    return getattr(request.state, "request_id", "")


def _log_event(level: int, event: str, request: Optional[Request] = None, **fields) -> None:
    extra = {
        "event": event,
        "request_id": _request_id(request),
        **fields,
    }
    if request is not None:
        extra.setdefault("path", request.url.path)
        extra.setdefault("method", request.method)
        extra.setdefault("host", request.headers.get("host", ""))
    logger.log(level, event, extra=extra)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _audit_event(
    event: str,
    request: Optional[Request],
    *,
    user_id: str = "",
    tenant_id: str = "",
    reason: str = "",
) -> None:
    logger.info(
        "audit_event",
        extra={
            "event": event,
            "timestamp": _utc_now_iso(),
            "user_id": user_id or "",
            "tenant_id": tenant_id or "",
            "path": request.url.path if request is not None else "",
            "method": request.method if request is not None else "",
            "ip": _client_ip(request),
            "reason": reason or "",
            "request_id": _request_id(request),
        },
    )


@app.middleware("http")
async def attach_request_context(request: Request, call_next):
    request_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or str(uuid.uuid4())
    )
    request.state.request_id = request_id
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        _log_event(
            logging.ERROR,
            "request_unhandled_exception",
            request=request,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        raise

    response.headers["x-request-id"] = request_id
    latency_seconds = time.perf_counter() - started_at
    endpoint = _endpoint_label(request)
    _metric_inc(
        "ztam_auth_http_requests_total",
        endpoint=endpoint,
        method=request.method,
        status_code=response.status_code,
    )
    _metric_observe_latency(
        "ztam_auth_http_request_duration_seconds",
        latency_seconds,
        endpoint=endpoint,
        method=request.method,
    )
    if request.url.path != "/health":
        _log_event(
            logging.INFO,
            "request_completed",
            request=request,
            status_code=response.status_code,
            latency_ms=round(latency_seconds * 1000, 2),
        )
    return response


@app.middleware("http")
async def enforce_envoy_shared_secret(request: Request, call_next):
    if request.url.path not in {"/health", "/metrics"}:
        supplied = request.headers.get("x-ztam-internal-auth", "")
        if supplied != ENVOY_AUTH_SHARED_SECRET:
            _audit_event(
                "deny",
                request,
                reason="missing_or_invalid_internal_shared_secret",
            )
            return Response(
                content='{"error":"forbidden"}',
                status_code=403,
                media_type="application/json",
            )
    return await call_next(request)

# ─── Config from environment ─────────────────────────────────────────────────
KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KC_REALM: str = os.getenv("KC_REALM", "test-tenant")
KC_CLIENT_ID: str = os.getenv("KC_CLIENT_ID", "test-app")
KC_CLIENT_SECRET: str = os.getenv("KC_CLIENT_SECRET", "")
OPA_URL: str = os.getenv("OPA_URL", "http://opa:8181")
TESTAPP_JWT_SECRET: Optional[str] = os.getenv("TESTAPP_JWT_SECRET")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
ENVOY_AUTH_SHARED_SECRET: str = os.getenv("ENVOY_AUTH_SHARED_SECRET", "")
OPA_TIMEOUT_SECONDS: float = float(os.getenv("OPA_TIMEOUT_SECONDS", "2.0"))
HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5.0"))
JWKS_TTL: int = int(os.getenv("JWKS_TTL_SECONDS", "300"))
REDIS_RETRY_ATTEMPTS: int = int(os.getenv("REDIS_RETRY_ATTEMPTS", "4"))
HTTP_RETRY_ATTEMPTS: int = int(os.getenv("HTTP_RETRY_ATTEMPTS", "4"))
# KC_ISSUER_URL: the public-facing URL Keycloak uses in the `iss` claim.
# Typically differs from KEYCLOAK_URL (internal Docker hostname) when
# Keycloak is configured with KC_HOSTNAME pointing to the public host.
KC_ISSUER_URL: str = os.getenv("KC_ISSUER_URL", KEYCLOAK_URL)

# Public URL used in redirects back to the browser (FQDN of the ZTAM gateway)
ZTAM_PUBLIC_URL: str = os.getenv("ZTAM_PUBLIC_URL", "https://localhost")

# Cookie settings
AUTH_COOKIE_NAME = "ztam_auth"
AUTH_ID_COOKIE_NAME = "ztam_id_token"
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
TENANTS_DIR = Path(os.getenv("TENANTS_DIR", "/app/tenants"))
AUTH_METADATA_FILE = Path(
    os.getenv("AUTH_METADATA_FILE", "/app/platform/published/auth/tenants.json")
)

# ─── JWKS in-memory cache with asyncio lock (prevents thundering herd) ───────
_jwks_cache: dict[str, dict] = {}
_jwks_fetched_at: dict[str, float] = {}
_jwks_lock = asyncio.Lock()
_redis: Optional[Redis] = None

_ALLOWED_ROLES = {"admin", "editor", "user", "viewer"}
_TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# ─── Startup validation — crash immediately if required secrets are empty ─────
if not KC_CLIENT_SECRET:
    raise RuntimeError(
        "FATAL: required environment variable 'KC_CLIENT_SECRET' is not set. "
        "Refusing to start."
    )
if not TESTAPP_JWT_SECRET:
    raise RuntimeError(
        "FATAL: required environment variable 'TESTAPP_JWT_SECRET' is not set. "
        "Refusing to start."
    )
if not ENVOY_AUTH_SHARED_SECRET:
    raise RuntimeError(
        "FATAL: required environment variable 'ENVOY_AUTH_SHARED_SECRET' is not set. "
        "Refusing to start."
    )

# ─── In-memory login rate limiter (per source IP) ─────────────────────────────
_rl_lock = threading.Lock()
_rl_state: dict = {}           # ip → {"count": int, "reset_at": float}
_LOGIN_RATE_LIMIT = 10         # max attempts per window
_LOGIN_RATE_WINDOW = 60.0      # seconds

_tenant_cache_lock = threading.Lock()
_tenant_cache_by_name: dict[str, dict] = {}
_tenant_cache_by_host: dict[str, dict] = {}
_tenant_cache_fetched_at: float = 0.0
TENANT_CACHE_TTL = 5.0

_metrics_lock = threading.Lock()
_metrics_counters: collections.Counter = collections.Counter()
_METRIC_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _metric_label(value: object) -> str:
    raw = str(value if value not in (None, "") else "unknown")
    return raw.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _metric_key(name: str, labels: dict[str, object]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((key, _metric_label(value)) for key, value in labels.items()))


def _metric_inc(name: str, value: float = 1.0, **labels) -> None:
    with _metrics_lock:
        _metrics_counters[_metric_key(name, labels)] += value


def _metric_observe_latency(name: str, seconds: float, **labels) -> None:
    with _metrics_lock:
        _metrics_counters[_metric_key(f"{name}_count", labels)] += 1
        _metrics_counters[_metric_key(f"{name}_sum", labels)] += seconds
        for bucket in _METRIC_LATENCY_BUCKETS:
            if seconds <= bucket:
                _metrics_counters[_metric_key(f"{name}_bucket", {**labels, "le": bucket})] += 1
        _metrics_counters[_metric_key(f"{name}_bucket", {**labels, "le": "+Inf"})] += 1


def _format_metric_line(name: str, value: float, labels: tuple[tuple[str, str], ...]) -> str:
    if labels:
        rendered = ",".join(f'{key}="{label}"' for key, label in labels)
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def _render_metrics() -> str:
    lines = [
        "# HELP ztam_auth_http_requests_total Total HTTP requests handled by auth-middleware.",
        "# TYPE ztam_auth_http_requests_total counter",
        "# HELP ztam_auth_http_request_duration_seconds Request duration seen by auth-middleware.",
        "# TYPE ztam_auth_http_request_duration_seconds histogram",
        "# HELP ztam_auth_login_attempts_total Login attempts by flow and outcome.",
        "# TYPE ztam_auth_login_attempts_total counter",
        "# HELP ztam_auth_decisions_total Authorization decisions by outcome and tenant.",
        "# TYPE ztam_auth_decisions_total counter",
        "# HELP ztam_auth_failures_total Authentication and policy-engine failures.",
        "# TYPE ztam_auth_failures_total counter",
    ]
    with _metrics_lock:
        items = sorted(_metrics_counters.items(), key=lambda item: (item[0][0], item[0][1]))
    for (name, labels), value in items:
        lines.append(_format_metric_line(name, value, labels))
    return "\n".join(lines) + "\n"


def _endpoint_label(request: Request) -> str:
    path = request.url.path
    if path == "/health":
        return "health"
    if path == "/metrics":
        return "metrics"
    if path == "/ztam/login":
        return "platform_login"
    if path == "/login-proxy":
        return "login_proxy"
    if path == "/logout":
        return "logout"
    if path in {"/ztam/login-redirect", "/login-redirect"}:
        return "login_redirect"
    if path in {"/ztam/auth/callback", "/api/auth/callback", "/auth/callback"}:
        return "auth_callback"
    return "ext_authz"


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    with _rl_lock:
        entry = _rl_state.get(client_ip)
        if entry is None or now >= entry["reset_at"]:
            _rl_state[client_ip] = {"count": 1, "reset_at": now + _LOGIN_RATE_WINDOW}
            return True
        if entry["count"] >= _LOGIN_RATE_LIMIT:
            return False
        entry["count"] += 1
        return True


def _cleanup_rate_limiter() -> None:
    """Remove expired rate-limit entries to prevent unbounded memory growth."""
    now = time.time()
    with _rl_lock:
        expired = [ip for ip, s in _rl_state.items() if now >= s["reset_at"]]
        for ip in expired:
            del _rl_state[ip]
    _log_event(logging.DEBUG, "rate_limiter_cleanup", removed_entries=len(expired))


def _load_tenant_cache() -> tuple[dict[str, dict], dict[str, dict]]:
    global _tenant_cache_by_name, _tenant_cache_by_host, _tenant_cache_fetched_at

    now = time.time()
    if _tenant_cache_by_name and (now - _tenant_cache_fetched_at) < TENANT_CACHE_TTL:
        return _tenant_cache_by_name, _tenant_cache_by_host

    with _tenant_cache_lock:
        now = time.time()
        if _tenant_cache_by_name and (now - _tenant_cache_fetched_at) < TENANT_CACHE_TTL:
            return _tenant_cache_by_name, _tenant_cache_by_host

        by_name: dict[str, dict] = {}
        by_host: dict[str, dict] = {}

        if AUTH_METADATA_FILE.exists():
            try:
                bundle = json.loads(AUTH_METADATA_FILE.read_text(encoding="utf-8"))
                for item in bundle.get("tenants", []):
                    tenant_name = str(item.get("tenant_id", "")).strip()
                    hostname = str(item.get("primary_hostname", "")).strip().split(":")[0]
                    if not tenant_name or not hostname:
                        continue
                    tenant_config = {
                        "name": tenant_name,
                        "hostname": hostname,
                        "login_mode": "keycloak"
                        if str(item.get("integration_mode", "managed_oidc")).strip() == "managed_oidc"
                        else "form",
                        "integration_mode": str(item.get("integration_mode", "managed_oidc")).strip(),
                        "identity_mode": str(item.get("identity_mode", "managed")).strip(),
                        "adapter_mode": str(item.get("adapter_mode", "headers")).strip() or "headers",
                        "status": str(item.get("status", "draft")).strip() or "draft",
                        "keycloak_client_id": str(item.get("keycloak_client_id", tenant_name)).strip() or tenant_name,
                        "keycloak_client_secret": "",
                        "downstream_jwt_secret": "",
                        "keycloak_realm": str(item.get("keycloak_realm", KC_REALM)).strip() or KC_REALM,
                        "source": "published_bundle",
                    }
                    by_name[tenant_name] = tenant_config
                    by_host[hostname.lower()] = tenant_config
            except Exception as exc:
                _log_event(
                    logging.WARNING,
                    "auth_metadata_bundle_unreadable",
                    auth_metadata_file=str(AUTH_METADATA_FILE),
                    error=str(exc),
                )

        if TENANTS_DIR.exists():
            for config_path in sorted(TENANTS_DIR.glob("*/config.json")):
                if config_path.parent.name == "_template":
                    continue
                try:
                    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    _log_event(logging.WARNING, "tenant_config_unreadable", config_path=str(config_path), error=str(exc))
                    continue

                tenant_name = str(raw_config.get("name", "")).strip()
                hostname = str(raw_config.get("hostname", "")).strip().split(":")[0]
                if not tenant_name or not hostname:
                    continue

                tenant_config = {
                    "name": tenant_name,
                    "hostname": hostname,
                    "login_mode": str(raw_config.get("login_mode", "form")).strip() or "form",
                    "integration_mode": "managed_oidc"
                    if str(raw_config.get("login_mode", "form")).strip() == "keycloak"
                    else "form_bridge",
                    "identity_mode": "managed" if bool(raw_config.get("no_spi", False)) else "federated_db",
                    "adapter_mode": str(raw_config.get("adapter_mode", "headers")).strip() or "headers",
                    "status": "published",
                    "keycloak_client_id": str(raw_config.get("keycloak_client_id", tenant_name)).strip() or tenant_name,
                    "keycloak_client_secret": str(raw_config.get("keycloak_client_secret", "")).strip(),
                    "downstream_jwt_secret": str(raw_config.get("downstream_jwt_secret", "")).strip(),
                    "keycloak_realm": str(raw_config.get("keycloak_realm", KC_REALM)).strip() or KC_REALM,
                    "source": "legacy_config",
                }
                by_name[tenant_name] = tenant_config
                by_host[hostname.lower()] = tenant_config

        _tenant_cache_by_name = by_name
        _tenant_cache_by_host = by_host
        _tenant_cache_fetched_at = now
        return _tenant_cache_by_name, _tenant_cache_by_host


def get_tenant_config(host_header: str = "", tenant_name: str = "") -> dict | None:
    tenants_by_name, tenants_by_host = _load_tenant_cache()
    host_clean = host_header.split(":")[0].strip().lower()
    tenant = None
    if host_clean and host_clean in tenants_by_host:
        tenant = tenants_by_host[host_clean]
    elif tenant_name:
        tenant = tenants_by_name.get(tenant_name)
    if tenant and tenant.get("status") == "disabled":
        return None
    return tenant


def build_callback_url(request: Request, tenant_name: str = "") -> str:
    host_header = request.headers.get("host", "").split(":")[0].strip()
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    base_url = f"{scheme}://{host_header}" if host_header else ZTAM_PUBLIC_URL.rstrip("/")
    if tenant_name:
        return f"{base_url}/ztam/auth/callback?tenant={tenant_name}"
    return f"{base_url}/ztam/auth/callback"


def _safe_next_url(next_url: str) -> str:
    if not next_url.startswith("/") or "//" in next_url:
        return "/"
    return next_url


def build_logged_out_url(request: Request, next_url: str = "/") -> str:
    host_header = request.headers.get("host", "").split(":")[0].strip()
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    base_url = f"{scheme}://{host_header}" if host_header else ZTAM_PUBLIC_URL.rstrip("/")
    return f"{base_url}/ztam/logged-out?{urlencode({'next': _safe_next_url(next_url)})}"


def build_keycloak_logout_url(
    request: Request,
    tenant_config: Optional[dict],
    *,
    next_url: str = "/",
    id_token_hint: str = "",
) -> str:
    realm = _tenant_realm(tenant_config)
    client_id = _tenant_client_id(tenant_config)
    params = {
        "post_logout_redirect_uri": build_logged_out_url(request, next_url),
        "client_id": client_id,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return (
        f"{KC_ISSUER_URL}/realms/{realm}/protocol/openid-connect/logout?"
        + urlencode(params)
    )


def _tenant_realm(tenant_config: Optional[dict]) -> str:
    return str((tenant_config or {}).get("keycloak_realm", KC_REALM)).strip() or KC_REALM


def _tenant_client_id(tenant_config: Optional[dict]) -> str:
    return str((tenant_config or {}).get("keycloak_client_id", KC_CLIENT_ID)).strip() or KC_CLIENT_ID


def _tenant_client_secret(tenant_config: Optional[dict]) -> str:
    return str((tenant_config or {}).get("keycloak_client_secret", KC_CLIENT_SECRET)).strip() or KC_CLIENT_SECRET


def _tenant_downstream_jwt_secret(tenant_config: Optional[dict]) -> str:
    secret = (tenant_config or {}).get("downstream_jwt_secret")
    return str(secret or TESTAPP_JWT_SECRET or "").strip()


def _realm_token_uri(realm: str) -> str:
    return f"{KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/token"


def _realm_jwks_uri(realm: str) -> str:
    return f"{KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/certs"


def _realm_expected_issuer(realm: str) -> str:
    return f"{KC_ISSUER_URL}/realms/{realm}"


def _kc_internal_roles(realm: str) -> frozenset[str]:
    return frozenset({
        "offline_access",
        "uma_authorization",
        f"default-roles-{realm}",
    })


@app.on_event("startup")
async def _start_rl_cleanup() -> None:
    client = await _redis_client()
    try:
        await client.ping()
    except Exception as exc:
        raise RuntimeError(f"FATAL: redis unavailable at startup ({REDIS_URL}): {exc}") from exc

    async def _loop():
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            _cleanup_rate_limiter()
    asyncio.create_task(_loop())


@app.on_event("shutdown")
async def _close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


def _normalize_path(path: str, query: str = "") -> str:
    raw = path or "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    parts = urlsplit(raw)
    clean_path = "/" + "/".join([p for p in parts.path.split("/") if p and p != "."])
    while "//" in clean_path:
        clean_path = clean_path.replace("//", "/")
    if ".." in clean_path.split("/"):
        clean_path = "/"
    safe_query = query or parts.query
    return urlunsplit(("", "", clean_path or "/", safe_query, ""))


def _sanitize_tenant_id(candidate: str, fallback: str) -> str:
    lowered = (candidate or "").strip().lower()
    if lowered and _TENANT_ID_RE.match(lowered):
        return lowered
    fallback_lowered = (fallback or "unknown").strip().lower()
    return fallback_lowered if _TENANT_ID_RE.match(fallback_lowered) else "unknown"


def _sanitize_roles(raw_roles: list[str]) -> list[str]:
    roles = [str(role).strip().lower() for role in raw_roles]
    allowed = [role for role in roles if role in _ALLOWED_ROLES]
    return list(dict.fromkeys(allowed))


def _extract_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    cookie_value = request.cookies.get(AUTH_COOKIE_NAME, "")
    return cookie_value.strip() or None


async def _redis_client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    return _redis


def _backoff_seconds(attempt: int) -> float:
    return min(2.0, 0.2 * (2 ** attempt))


async def _request_with_retries(
    method: str,
    url: str,
    *,
    request: Optional[Request] = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    retry_attempts: int = HTTP_RETRY_ATTEMPTS,
    **kwargs,
) -> httpx.Response:
    last_error: Optional[Exception] = None
    for attempt in range(retry_attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, **kwargs)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"upstream returned {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                return response
        except Exception as exc:
            last_error = exc
            if attempt == retry_attempts - 1:
                break
            wait_seconds = _backoff_seconds(attempt)
            _log_event(
                logging.WARNING,
                "upstream_request_retry",
                request=request,
                upstream_url=url,
                attempt=attempt + 1,
                wait_seconds=wait_seconds,
                error=str(exc),
            )
            await asyncio.sleep(wait_seconds)
    raise RuntimeError(f"upstream request failed after retries: {url}") from last_error


async def _redis_get_with_retries(key: str) -> Optional[str]:
    last_error: Optional[Exception] = None
    for attempt in range(REDIS_RETRY_ATTEMPTS):
        try:
            client = await _redis_client()
            return await client.get(key)
        except Exception as exc:
            last_error = exc
            if attempt == REDIS_RETRY_ATTEMPTS - 1:
                break
            await asyncio.sleep(_backoff_seconds(attempt))
    raise RuntimeError(f"redis get failed for key {key}") from last_error


async def _redis_setex_with_retries(key: str, ttl_seconds: int, value: str) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(REDIS_RETRY_ATTEMPTS):
        try:
            client = await _redis_client()
            await client.setex(key, ttl_seconds, value)
            return
        except Exception as exc:
            last_error = exc
            if attempt == REDIS_RETRY_ATTEMPTS - 1:
                break
            await asyncio.sleep(_backoff_seconds(attempt))
    raise RuntimeError(f"redis setex failed for key {key}") from last_error


async def get_jwks(realm: str) -> dict:
    """Fetch and cache Keycloak JWKS in Redis + local memory cache."""
    global _jwks_cache, _jwks_fetched_at

    now = time.time()
    cached = _jwks_cache.get(realm)
    fetched_at = _jwks_fetched_at.get(realm, 0.0)
    if cached and (now - fetched_at) < JWKS_TTL:
        return cached

    async with _jwks_lock:
        now = time.time()
        cached = _jwks_cache.get(realm)
        fetched_at = _jwks_fetched_at.get(realm, 0.0)
        if cached and (now - fetched_at) < JWKS_TTL:
            return cached

        cache_key = f"ztam:jwks:{realm}"
        cached_value = await _redis_get_with_retries(cache_key)
        if cached_value:
            _jwks_cache[realm] = json.loads(cached_value)
            _jwks_fetched_at[realm] = time.time()
            return _jwks_cache[realm]

        jwks_uri = _realm_jwks_uri(realm)
        _log_event(logging.INFO, "jwks_fetch_started", jwks_uri=jwks_uri, realm=realm)
        response = await _request_with_retries("GET", jwks_uri, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        _jwks_cache[realm] = response.json()
        _jwks_fetched_at[realm] = time.time()
        await _redis_setex_with_retries(cache_key, JWKS_TTL, json.dumps(_jwks_cache[realm]))
        return _jwks_cache[realm]


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
    realm = str(claims.get("iss", "")).rstrip("/").split("/")[-1] or KC_REALM
    internal_roles = _kc_internal_roles(realm)
    return [r for r in dict.fromkeys(roles) if r and r not in internal_roles]


# ─── Endpoints ───────────────────────────────────────────────────────────────

# ─── ZTAM Platform Login (Direct Redirect to Keycloak) ────────────────────────
# This endpoint now redirects the browser directly to Keycloak's original
# login interface, providing a more secure and consistent experience.
# Previous custom HTML form (_ZTAM_LOGIN_HTML) has been removed.

_ZTAM_DENIED_HTML = """\
<!DOCTYPE html><html lang="en"><head>
  <meta charset="UTF-8"><title>ZTAM — Access Denied</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
      min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .card { background: #1e293b; border: 1px solid #7f1d1d; border-radius: 12px;
      padding: 2.5rem; text-align: center; max-width: 400px; }
    h1 { font-size: 3rem; margin-bottom: .5rem }
    h2 { color: #f43f5e; margin-bottom: 1rem }
    p { color: #94a3b8; margin-bottom: 1.5rem; font-size: .9rem }
    a { color: #3b82f6; text-decoration: none; font-size: .9rem }
  </style>
</head><body><div class="card">
  <h1>&#128274;</h1>
  <h2>Access Denied</h2>
  <p>Your role does not permit access to this resource.</p>
  <a href="/ztam/login">&#8592; Sign in with a different account</a>
</div></body></html>
"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(
        content=_render_metrics(),
        media_type="text/plain",
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )


@app.get("/ztam/login")
async def ztam_login_page(request: Request, next: str = "/") -> Response:
    """Redirect to Keycloak's original login interface."""
    # Try to identify tenant from Host header to use correct realm/client
    host_header = request.headers.get("host", "")
    tenant_config = get_tenant_config(host_header)
    
    tenant_name = (tenant_config or {}).get("name", "")
    client_id = _tenant_client_id(tenant_config)
    realm = _tenant_realm(tenant_config)
    callback_url = build_callback_url(request, tenant_name)
    
    auth_uri = (
        f"{KC_ISSUER_URL}/realms/{realm}/protocol/openid-connect/auth?"
        + urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "scope": "openid profile email",
                "redirect_uri": callback_url,
                "state": next,
            }
        )
    )
    _log_event(logging.INFO, "ztam_login_redirect_to_keycloak", request=request, tenant=tenant_name, redirect_to=auth_uri)
    return Response(status_code=302, headers={"Location": auth_uri})


@app.post("/ztam/login")
async def ztam_login_post(request: Request) -> Response:
    """
    Platform login handler: authenticates via Keycloak and sets a secure
    HttpOnly cookie so every subsequent Envoy ext_authz check passes without
    any client-side token management.
    """
    try:
        body = await request.json()
    except Exception:
        return Response(content='{"error":"invalid request body"}',
                        status_code=400, media_type="application/json")

    username: str = body.get("username", "").strip()
    password: str = body.get("password", "")
    next_url: str = _safe_next_url(body.get("next", "/"))
    tenant_config = get_tenant_config(request.headers.get("host", ""))
    realm = _tenant_realm(tenant_config)
    client_id = _tenant_client_id(tenant_config)
    client_secret = _tenant_client_secret(tenant_config)

    if not username or not password:
        return Response(content='{"error":"Username and password are required"}',
                        status_code=400, media_type="application/json")
    if len(username) > 200 or len(password) > 1000:
        return Response(content='{"error":"Invalid credentials"}',
                        status_code=400, media_type="application/json")

    client_ip: str = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        _metric_inc("ztam_auth_login_attempts_total", flow="platform", outcome="rate_limited")
        _log_event(logging.WARNING, "platform_login_rate_limited", request=request, client_ip=client_ip)
        _audit_event("login_failure", request, user_id=username, reason="rate_limited")
        return Response(content='{"error":"Too many login attempts, please try again later"}',
                        status_code=429, media_type="application/json")

    try:
        kc_resp = await _request_with_retries(
            "POST",
            _realm_token_uri(realm),
            request=request,
            timeout=HTTP_TIMEOUT_SECONDS,
            data={
                "grant_type": "password",
                "client_id": client_id,
                "client_secret": client_secret,
                "username": username,
                "password": password,
            },
        )
    except Exception as exc:
        _metric_inc("ztam_auth_failures_total", type="keycloak_unreachable", flow="platform")
        _log_event(logging.ERROR, "platform_login_keycloak_unreachable", request=request, client_ip=client_ip, error=str(exc))
        _audit_event("login_failure", request, user_id=username, reason="keycloak_unreachable")
        return Response(content='{"error":"Auth service unavailable"}',
                        status_code=503, media_type="application/json")

    if kc_resp.status_code != 200:
        _metric_inc("ztam_auth_login_attempts_total", flow="platform", outcome="failed")
        _log_event(logging.WARNING, "platform_login_failed", request=request, username=username, client_ip=client_ip, status_code=kc_resp.status_code)
        _audit_event("login_failure", request, user_id=username, reason="invalid_credentials")
        return Response(content='{"error":"Invalid credentials"}',
                        status_code=401, media_type="application/json")

    kc_data = kc_resp.json()
    kc_token: str = kc_data["access_token"]
    expires_in: int = kc_data.get("expires_in", 3600)

    try:
        claims = jwt.get_unverified_claims(kc_token)
    except JWTError as exc:
        _metric_inc("ztam_auth_failures_total", type="platform_token_decode_failed", flow="platform")
        _log_event(logging.ERROR, "platform_login_token_decode_failed", request=request, username=username, error=str(exc))
        return Response(content='{"error":"Auth service error"}',
                        status_code=500, media_type="application/json")

    role: str = claims.get("role") or "viewer"
    uname: str = claims.get("preferred_username", username)
    _metric_inc("ztam_auth_login_attempts_total", flow="platform", outcome="succeeded")
    _log_event(logging.INFO, "platform_login_succeeded", request=request, username=uname, role=role)
    _audit_event("login_success", request, user_id=str(claims.get("sub", uname)), reason="platform_login")

    resp = Response(
        content=json.dumps({"redirect": next_url, "username": uname, "role": role}),
        status_code=200,
        media_type="application/json",
    )
    resp.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=kc_token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=expires_in,
        path="/",
    )
    id_token = kc_data.get("id_token")
    if id_token:
        resp.set_cookie(
            key=AUTH_ID_COOKIE_NAME,
            value=id_token,
            httponly=True,
            secure=AUTH_COOKIE_SECURE,
            samesite=AUTH_COOKIE_SAMESITE,
            max_age=expires_in,
            path="/",
        )
    return resp


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
    tenant_config = get_tenant_config(request.headers.get("host", ""))
    realm = _tenant_realm(tenant_config)
    client_id = _tenant_client_id(tenant_config)
    client_secret = _tenant_client_secret(tenant_config)

    if not username or not password:
        return Response(
            content='{"error":"username and password are required."}',
            status_code=400, media_type="application/json"
        )

    # ── Input length limits (prevent oversized-payload DoS) ──────────────────
    if len(username) > 200 or len(password) > 1000:
        return Response(
            content='{"error":"invalid credentials."}',
            status_code=400, media_type="application/json"
        )

    # ── Per-IP rate limit: max 10 login attempts per 60 s ────────────────────
    client_ip: str = (request.client.host if request.client else "unknown")
    if not _check_rate_limit(client_ip):
        _metric_inc("ztam_auth_login_attempts_total", flow="proxy", outcome="rate_limited")
        _log_event(logging.WARNING, "login_proxy_rate_limited", request=request, client_ip=client_ip)
        _audit_event("login_failure", request, user_id=username, reason="rate_limited")
        return Response(
            content='{"error":"too many login attempts, please try again later."}',
            status_code=429, media_type="application/json"
        )

    # ── 1. Authenticate via Keycloak token endpoint ───────────────────────────
    try:
        kc_resp = await _request_with_retries(
            "POST",
            _realm_token_uri(realm),
            request=request,
            timeout=HTTP_TIMEOUT_SECONDS,
            data={
                "grant_type": "password",
                "client_id": client_id,
                "client_secret": client_secret,
                "username": username,
                "password": password,
            },
        )
    except Exception as exc:
        _metric_inc("ztam_auth_failures_total", type="keycloak_unreachable", flow="proxy")
        _log_event(logging.ERROR, "login_proxy_keycloak_unreachable", request=request, client_ip=client_ip, error=str(exc))
        _audit_event("login_failure", request, user_id=username, reason="keycloak_unreachable")
        return Response(content='{"error":"auth service unavailable"}',
                        status_code=503, media_type="application/json")

    if kc_resp.status_code != 200:
        _metric_inc("ztam_auth_login_attempts_total", flow="proxy", outcome="failed")
        _log_event(logging.WARNING, "login_proxy_failed", request=request, username=username, client_ip=client_ip, status_code=kc_resp.status_code)
        _audit_event("login_failure", request, user_id=username, reason="invalid_credentials")
        return Response(content='{"error":"Invalid credentials."}',
                        status_code=401, media_type="application/json")

    kc_data = kc_resp.json()
    kc_token: str = kc_data["access_token"]

    # ── 2. Decode claims safely using python-jose (handles padding, structure) ─
    try:
        claims = jwt.get_unverified_claims(kc_token)
    except JWTError as exc:
        _metric_inc("ztam_auth_failures_total", type="proxy_token_decode_failed", flow="proxy")
        _log_event(logging.ERROR, "login_proxy_token_decode_failed", request=request, username=username, error=str(exc))
        return Response(content='{"error":"malformed token from IdP"}',
                        status_code=500, media_type="application/json")

    role: str = claims.get("role") or "viewer"
    uname: str = claims.get("preferred_username", username)

    _metric_inc("ztam_auth_login_attempts_total", flow="proxy", outcome="succeeded")
    _log_event(logging.INFO, "login_proxy_succeeded", request=request, username=uname, role=role)
    _audit_event("login_success", request, user_id=str(claims.get("sub", uname)), reason="proxy_login")

    # ── 3. Return RS256 Keycloak token to the browser ─────────────────────────
    # The browser stores this and sends it on every subsequent API request.
    # The check() handler below will validate it via JWKS and translate it to
    # an HS256 token before forwarding to TestApp.
    return Response(
        content=json.dumps({"token": kc_token, "username": uname, "role": role}),
        status_code=200,
        media_type="application/json",
    )


@app.post("/logout")
async def logout(request: Request) -> Response:
    """
    Server-side logout: revokes the Keycloak session so the token
    is invalidated even before it expires.
    Called by Envoy for POST /api/auth/logout.
    """
    token = _extract_token(request)

    host_header: str = request.headers.get("host", "")
    tenant_config = get_tenant_config(host_header)
    id_token_hint = request.cookies.get(AUTH_ID_COOKIE_NAME, "")
    logout_redirect = build_keycloak_logout_url(
        request,
        tenant_config,
        next_url="/dashboard.html",
        id_token_hint=id_token_hint,
    )
    response = Response(
        content=json.dumps({"message": "logged out", "logout_url": logout_redirect}),
        status_code=200,
        media_type="application/json",
    )
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(key=AUTH_ID_COOKIE_NAME, path="/")
    if not token:
        _log_event(logging.INFO, "logout_without_token", request=request)
        _audit_event("logout", request, reason="no_token")
        return response
    try:
        unverified = jwt.get_unverified_claims(token)
        exp = int(unverified.get("exp", 0))
        now = int(time.time())
        ttl = max(0, exp - now)
        token_jti = str(unverified.get("jti") or hashlib.sha256(token.encode("utf-8")).hexdigest())
        if ttl > 0:
            await _redis_setex_with_retries(f"ztam:blacklist:{token_jti}", ttl, "1")
        realm = _tenant_realm(tenant_config)
        client_id = _tenant_client_id(tenant_config)
        client_secret = _tenant_client_secret(tenant_config)
        await _request_with_retries(
            "POST",
            f"{KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/logout",
            request=request,
            timeout=HTTP_TIMEOUT_SECONDS,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "token_type_hint": "access_token",
                "token": token,
            },
        )
        _log_event(logging.INFO, "logout_succeeded", request=request)
        _audit_event("logout", request, user_id=str(unverified.get("sub", "")), tenant_id=str((tenant_config or {}).get("name", "")), reason="logout_succeeded")
    except Exception as exc:
        _log_event(logging.WARNING, "logout_keycloak_unreachable", request=request, error=str(exc))
        _audit_event("logout", request, reason="logout_partial_failure")
    return response


@app.get("/ztam/logout")
async def browser_logout(request: Request, next: str = "/") -> Response:
    """
    Browser logout: clear ZTAM cookies and redirect the browser through
    Keycloak's logout endpoint so the IdP session is cleared too.
    """
    next_url = _safe_next_url(next)
    host_header: str = request.headers.get("host", "")
    tenant_config = get_tenant_config(host_header)
    id_token_hint = request.cookies.get(AUTH_ID_COOKIE_NAME, "")
    logout_url = build_keycloak_logout_url(
        request,
        tenant_config,
        next_url=next_url,
        id_token_hint=id_token_hint,
    )
    response = Response(status_code=302, headers={"Location": logout_url})
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(key=AUTH_ID_COOKIE_NAME, path="/")
    _log_event(
        logging.INFO,
        "browser_logout_redirected",
        request=request,
        redirect_to=logout_url,
    )
    return response


@app.get("/ztam/logged-out")
async def logged_out_page(next: str = "/") -> Response:
    next_url = _safe_next_url(next)
    html = f"""\
<!DOCTYPE html><html lang="en"><head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ZTAM - Signed Out</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #f6f8fb; color: #0f172a;
      min-height: 100vh; display: flex; align-items: center; justify-content: center; margin: 0; }}
    .card {{ background: white; border-radius: 14px; box-shadow: 0 18px 50px rgba(15, 23, 42, .08);
      padding: 2rem; width: min(420px, calc(100vw - 2rem)); text-align: center; }}
    h1 {{ margin: 0 0 .75rem; font-size: 1.4rem; }}
    p {{ margin: 0 0 1.25rem; color: #475569; }}
    a {{ display: inline-block; background: #0f3460; color: white; text-decoration: none;
      padding: .8rem 1.1rem; border-radius: 10px; font-weight: 600; }}
  </style>
</head><body><div class="card">
  <h1>Signed out</h1>
  <p>Your ZTAM and Keycloak session has been closed.</p>
  <a href="/ztam/login?{urlencode({'next': next_url})}">Sign in again</a>
</div></body></html>
"""
    return Response(content=html, media_type="text/html")


@app.get("/ztam/login-redirect")
@app.get("/login-redirect")
async def login_redirect(request: Request, tenant: str, next: str = "/") -> Response:
    """
    Redirects the browser to Keycloak's login page for a specific tenant.
    Called by Envoy when a user hits a 'keycloak' login-mode tenant without a token.
    """
    tenant_config = get_tenant_config(tenant_name=tenant) or {}
    client_id = _tenant_client_id(tenant_config) if tenant_config else tenant
    realm = _tenant_realm(tenant_config)
    callback_url = build_callback_url(request, tenant)
    scoped_auth_uri = (
        f"{KC_ISSUER_URL}/realms/{realm}/protocol/openid-connect/auth?"
        + urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "scope": "openid profile email",
                "redirect_uri": callback_url,
                "state": next,
            }
        )
    )
    return Response(status_code=302, headers={"Location": scoped_auth_uri})


@app.get("/ztam/auth/callback")
@app.get("/api/auth/callback")
@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str = "/", tenant: str = "") -> Response:
    """
    OAuth2 callback: exchanges authorization code for a token,
    sets a secure cookie, and redirects back to the original app page.
    """
    tenant_config = get_tenant_config(request.headers.get("host", ""), tenant) or {}
    client_id = _tenant_client_id(tenant_config) if tenant_config else (tenant or KC_CLIENT_ID)
    client_secret = _tenant_client_secret(tenant_config)
    realm = _tenant_realm(tenant_config)
    token_uri = _realm_token_uri(realm)
    callback_url = build_callback_url(request, tenant_config.get("name", tenant))

    try:
        token_request = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": callback_url,
        }
        if client_secret:
            token_request["client_secret"] = client_secret
        resp = await _request_with_retries(
            "POST",
            token_uri,
            request=request,
            timeout=HTTP_TIMEOUT_SECONDS,
            data=token_request,
        )
        resp.raise_for_status()
        data = resp.json()
        access_token = data["access_token"]
        id_token = data.get("id_token", "")

        # Redirect back to the original page (stored in state)
        response = Response(status_code=302, headers={"Location": state})

        # Set secure HttpOnly cookie
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=access_token,
            httponly=True,
            secure=AUTH_COOKIE_SECURE,
            samesite=AUTH_COOKIE_SAMESITE,
            max_age=data.get("expires_in", 3600),
        )
        if id_token:
            response.set_cookie(
                key=AUTH_ID_COOKIE_NAME,
                value=id_token,
                httponly=True,
                secure=AUTH_COOKIE_SECURE,
                samesite=AUTH_COOKIE_SAMESITE,
                max_age=data.get("expires_in", 3600),
                path="/",
            )
        _metric_inc("ztam_auth_login_attempts_total", flow="callback", outcome="succeeded")
        _log_event(
            logging.INFO,
            "auth_callback_succeeded",
            request=request,
            tenant_id=tenant_config.get("name", tenant),
            client_id=client_id,
        )
        _audit_event(
            "login_success",
            request,
            tenant_id=str(tenant_config.get("name", tenant)),
            reason="callback",
        )
        return response
    except Exception as exc:
        _log_event(
            logging.ERROR,
            "auth_callback_failed",
            request=request,
            tenant_id=tenant_config.get("name", tenant),
            client_id=client_id,
            error=str(exc),
        )
        _metric_inc("ztam_auth_failures_total", type="callback_failed", tenant_id=tenant_config.get("name", tenant) or "unknown")
        _audit_event("login_failure", request, tenant_id=str(tenant_config.get("name", tenant)), reason="callback_failed")
        return Response(content='{"error":"authentication failed"}',
                        status_code=500, media_type="application/json")


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

    # ---------- 1. Determine original path early (needed for login redirect) -
    original_path: str = _normalize_path("/" + full_path if full_path else "/", request.url.query)

    if original_path == "/health":
        return Response(content='{"status":"ok"}', media_type="application/json")
    if original_path == "/metrics":
        return Response(
            content=_render_metrics(),
            media_type="text/plain",
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        )

    # ---------- 2. Extract Bearer token (Header first, Cookie fallback) -----
    token = _extract_token(request)

    host_header: str = request.headers.get("host", "")
    host_clean: str = host_header.split(":")[0]
    tenant_config = get_tenant_config(host_header)
    tenant_realm = _tenant_realm(tenant_config)
    tenant_client_id = _tenant_client_id(tenant_config)

    if not token:
        # Browser clients get redirected to the ZTAM login page.
        # API clients (no 'text/html' in Accept) get a 401 JSON response.
        accept: str = request.headers.get("accept", "")
        if "text/html" in accept:
            if tenant_config and tenant_config.get("login_mode") == "keycloak":
                login_url = f"/ztam/login-redirect?{urlencode({'tenant': tenant_config['name'], 'next': original_path})}"
            else:
                login_url = f"/ztam/login?{urlencode({'next': original_path})}"
            _metric_inc("ztam_auth_decisions_total", outcome="redirect_to_login", tenant_id=(tenant_config or {}).get("name", "unknown"))
            _log_event(logging.INFO, "auth_redirected_to_login", request=request, tenant_id=(tenant_config or {}).get("name", ""), redirect_to=login_url, path=original_path)
            _audit_event("deny", request, tenant_id=str((tenant_config or {}).get("name", "")), reason="redirect_to_login")
            return Response(status_code=302, headers={"location": login_url})
        _metric_inc("ztam_auth_decisions_total", outcome="missing_token", tenant_id=(tenant_config or {}).get("name", "unknown"))
        _log_event(logging.WARNING, "auth_missing_token", request=request, tenant_id=(tenant_config or {}).get("name", ""), path=original_path)
        _audit_event("deny", request, tenant_id=str((tenant_config or {}).get("name", "")), reason="missing_token")
        return Response(
            content='{"error":"missing token"}',
            status_code=401,
            media_type="application/json",
        )

    # ---------- 2 & 3. Fetch JWKS (cached, lock-protected) ------------------
    try:
        jwks = await get_jwks(tenant_realm)
    except Exception as exc:
        _metric_inc("ztam_auth_failures_total", type="jwks_fetch_failed")
        _log_event(logging.ERROR, "jwks_fetch_failed", request=request, error=str(exc), path=original_path)
        return Response(
            content='{"error":"auth service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    # ---------- 4 & 5. Validate RS256 JWT (signature + expiry + iss) ---------
    # Note: Keycloak access tokens carry aud="account", not the client ID.
    # We skip aud validation and instead check azp (authorized party) below.
    try:
        claims: dict = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=_realm_expected_issuer(tenant_realm),
        )
    except JWTError as exc:
        _metric_inc("ztam_auth_failures_total", type="jwt_validation_failed")
        _log_event(logging.WARNING, "jwt_validation_failed", request=request, error=str(exc), path=original_path)
        _audit_event("deny", request, reason="jwt_validation_failed")
        return Response(
            content='{"error":"invalid or expired token"}',
            status_code=403,
            media_type="application/json",
        )

    try:
        token_jti = str(claims.get("jti") or hashlib.sha256(token.encode("utf-8")).hexdigest())
        blacklisted = await _redis_get_with_retries(f"ztam:blacklist:{token_jti}")
        if blacklisted:
            _audit_event("deny", request, user_id=str(claims.get("sub", "")), reason="token_revoked")
            return Response(
                content='{"error":"token revoked"}',
                status_code=403,
                media_type="application/json",
            )
    except Exception as exc:
        _log_event(logging.ERROR, "redis_blacklist_check_failed", request=request, error=str(exc))
        return Response(
            content='{"error":"auth service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    azp = claims.get("azp", "")
    if not azp:
        _metric_inc("ztam_auth_failures_total", type="jwt_missing_azp")
        _log_event(logging.WARNING, "jwt_rejected_missing_azp", request=request, path=original_path)
        return Response(
            content='{"error":"invalid token: no authorized party"}',
            status_code=403,
            media_type="application/json",
        )
    if tenant_config and azp != tenant_client_id:
        _metric_inc("ztam_auth_failures_total", type="jwt_client_mismatch", tenant_id=(tenant_config or {}).get("name", "unknown"))
        _log_event(logging.WARNING, "jwt_client_mismatch", request=request, tenant_id=(tenant_config or {}).get("name", ""), azp=azp, expected_client_id=tenant_client_id)
        _audit_event("deny", request, tenant_id=str((tenant_config or {}).get("name", "")), reason="jwt_client_mismatch")
        return Response(
            content='{"error":"invalid token: client mismatch"}',
            status_code=403,
            media_type="application/json",
        )

    # ---------- 6. Extract user claims ---------------------------------------
    user_id: str     = claims.get("sub", "")
    email: str       = claims.get("email", "")

    # Derive tenant from the original virtual host (e.g. "store.ztam.local" → "store").
    # This lets OPA look up the correct per-tenant policies regardless of which
    # Keycloak client was used for authentication.
    tenant_from_host: str = (
        tenant_config["name"]
        if tenant_config
        else (host_clean.split(".")[0] if "." in host_clean else "")
    )
    tenant_id: str = _sanitize_tenant_id(
        tenant_from_host or str(claims.get("tenant_id") or claims.get("azp") or ""),
        (tenant_config or {}).get("name", tenant_realm),
    )
    roles: list[str] = _sanitize_roles(extract_roles(claims))

    if not user_id:
        return Response(
            content='{"error":"invalid token: missing sub"}',
            status_code=403,
            media_type="application/json",
        )

    if not roles:
        roles = ["viewer"]

    # ---------- 7. Build OPA input -------------------------------------------
    original_method: str = request.method.upper()

    client_type = "browser" if "text/html" in request.headers.get("accept", "") else "api"
    clean_path = _normalize_path("/" + full_path if full_path else "/")
    opa_input = {
        "input": {
            "tenant": {
                "id": tenant_id,
                "integration_mode": (tenant_config or {}).get("integration_mode", "form_bridge"),
                "identity_mode": (tenant_config or {}).get("identity_mode", "managed"),
            },
            "subject": {
                "id": user_id,
                "email": email,
                "roles": roles,
            },
            "request": {
                "path": clean_path,
                "method": original_method,
            },
            "client": {
                "type": client_type,
                "host": host_clean,
            },
            "device": {
                "posture": "unknown",
            },
            # Compatibility bridge while the policy contract migrates.
            "user": {
                "id": user_id,
                "email": email,
                "roles": roles,
                "tenant_id": tenant_id,
            },
        }
    }

    # ---------- 8. Single OPA call — get allow + deny_reason together --------
    try:
        opa_resp = await _request_with_retries(
            "POST",
            f"{OPA_URL}/v1/data/authz",
            request=request,
            timeout=OPA_TIMEOUT_SECONDS,
            json=opa_input,
        )
        opa_resp.raise_for_status()
        opa_result: dict = opa_resp.json().get("result", {})
    except Exception as exc:
        _metric_inc("ztam_auth_failures_total", type="opa_call_failed", tenant_id=tenant_id)
        _log_event(logging.ERROR, "opa_call_failed", request=request, tenant_id=tenant_id, path=original_path, error=str(exc))
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
        adapter_mode = str((tenant_config or {}).get("adapter_mode", "headers")).strip() or "headers"
        downstream_jwt_secret = _tenant_downstream_jwt_secret(tenant_config)
        if adapter_mode == "translated_token" and downstream_jwt_secret:
            now_ts = int(time.time())
            db_user_id = claims.get("db_user_id")
            try:
                db_user_id_int = int(str(db_user_id))
            except Exception:
                _audit_event("deny", request, user_id=user_id, tenant_id=tenant_id, reason="invalid_db_user_id_for_translation")
                return Response(
                    content='{"error":"invalid token: missing numeric db_user_id"}',
                    status_code=403,
                    media_type="application/json",
                )
            downstream_token = jwt.encode(
                {
                    "sub":      db_user_id_int,
                    "username": username,
                    "role":     primary_role,
                    "iat":      now_ts,
                    "exp":      now_ts + 3600,
                },
                downstream_jwt_secret,
                algorithm="HS256",
            )
            extra_headers["authorization"] = f"Bearer {downstream_token}"
            _log_event(
                logging.INFO,
                "access_allowed_token_translated",
                request=request,
                tenant_id=tenant_id,
                user_id=user_id,
                username=username,
                role=primary_role,
                path=original_path,
            )
        _metric_inc("ztam_auth_decisions_total", outcome="allowed", tenant_id=tenant_id)
        _audit_event("allow", request, user_id=user_id, tenant_id=tenant_id, reason="policy_allow")

        # Build downstream roles list.
        # Prefer the SPI-assigned custom_role (authoritative application role).
        # Fall back to the filtered realm/client roles for non-SPI tenants.
        if custom_role:
            clean_roles = _sanitize_roles([primary_role])
        else:
            clean_roles = _sanitize_roles([r for r in roles if r not in _kc_internal_roles(tenant_realm)])
        if not clean_roles:
            clean_roles = ["viewer"]
        return Response(
            status_code=200,
            headers={
                "x-user-id":    user_id,
                "x-username":   username,
                "x-user-roles": ",".join(clean_roles),
                "x-tenant-id":  tenant_id,
                "x-request-id": _request_id(request),
                **extra_headers,
            },
        )

    # ---------- 10. Deny ----------------------------------------------------
    _log_event(
        logging.INFO,
        "access_denied",
        request=request,
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles,
        path=original_path,
        reason=deny_reason,
    )
    _metric_inc("ztam_auth_decisions_total", outcome="denied", tenant_id=tenant_id)
    _audit_event("deny", request, user_id=user_id, tenant_id=tenant_id, reason=deny_reason)
    # Return a user-friendly HTML page for browser requests
    accept: str = request.headers.get("accept", "")
    if "text/html" in accept:
        return Response(content=_ZTAM_DENIED_HTML, status_code=403, media_type="text/html")
    return Response(
        content=json.dumps({"error": "access denied", "reason": deny_reason}),
        status_code=403,
        media_type="application/json",
    )
