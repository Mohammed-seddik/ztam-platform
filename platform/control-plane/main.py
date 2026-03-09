import json
import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from tenant_manager import (  # type: ignore
    CLUSTERS_BEGIN_MARKER,
    CLUSTERS_END_MARKER,
    ROUTES_BEGIN_MARKER,
    ROUTES_END_MARKER,
    _replace_generated_block,
    assess_backend,
    derive_backend_parts,
    normalize_permissions,
    parse_roles,
    render_tenant_cluster,
    render_tenant_vhost,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("control-plane")

DB_PATH = Path(os.getenv("CONTROL_PLANE_DB_PATH", str(REPO_ROOT / "build-verify" / "control-plane.db")))
TENANTS_DIR = Path(os.getenv("TENANTS_DIR", str(REPO_ROOT / "tenants")))
PUBLISHED_DIR = Path(os.getenv("PUBLISHED_DIR", str(REPO_ROOT / "platform" / "published")))
POLICIES_FILE = Path(os.getenv("POLICIES_FILE", str(REPO_ROOT / "policies" / "tenants.json")))
ENVOY_YAML = Path(os.getenv("ENVOY_YAML", str(REPO_ROOT / "envoy" / "envoy.yaml")))

AUTH_BUNDLE_FILE = PUBLISHED_DIR / "auth" / "tenants.json"
ROUTING_BUNDLE_FILE = PUBLISHED_DIR / "routing" / "tenants.json"
POLICY_BUNDLE_FILE = PUBLISHED_DIR / "policy" / "tenants.json"

SUPPORTED_INTEGRATION_MODES = {"managed_oidc", "form_bridge", "federated_db"}
SUPPORTED_IDENTITY_MODES = {"managed", "federated_db"}

app = FastAPI(title="ZTAM Control Plane", version="1.0.0")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@contextmanager
def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
              tenant_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              tenant_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              action TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )


def load_all_tenants() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT payload FROM tenants ORDER BY tenant_id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def load_tenant(tenant_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT payload FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"tenant not found: {tenant_id}")
    return json.loads(row["payload"])


def save_tenant(payload: dict[str, Any], action: str) -> dict[str, Any]:
    tenant_id = payload["tenant_id"]
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tenants (tenant_id, payload) VALUES (?, ?)",
            (tenant_id, json.dumps(payload)),
        )
        conn.execute(
            """
            INSERT INTO tenant_revisions (tenant_id, revision, action, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tenant_id, payload["revision"], action, json.dumps(payload), utc_now()),
        )
    logger.info("tenant_%s tenant_id=%s revision=%s", action, tenant_id, payload["revision"])
    return payload


def load_revisions(tenant_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT revision, action, payload, created_at
            FROM tenant_revisions
            WHERE tenant_id = ?
            ORDER BY revision DESC, id DESC
            """,
            (tenant_id,),
        ).fetchall()
    return [
        {
            "revision": row["revision"],
            "action": row["action"],
            "created_at": row["created_at"],
            "payload": json.loads(row["payload"]),
        }
        for row in rows
    ]


def hostname_exists(hostname: str, *, excluding_tenant_id: str = "") -> bool:
    for tenant in load_all_tenants():
        if tenant["primary_hostname"].lower() == hostname.lower() and tenant["tenant_id"] != excluding_tenant_id:
            return True
    return False


def integration_to_login_mode(integration_mode: str) -> str:
    if integration_mode == "managed_oidc":
        return "keycloak"
    return "form"


def tenant_to_runtime_shape(tenant: dict[str, Any]) -> dict[str, Any]:
    backend_host, backend_port, backend_tls = derive_backend_parts(tenant["backend_origin"])
    return {
        "name": tenant["tenant_id"],
        "hostname": tenant["primary_hostname"],
        "backend_url": tenant["backend_origin"],
        "backend_host": backend_host,
        "backend_port": backend_port,
        "backend_tls": backend_tls,
        "login_mode": integration_to_login_mode(tenant["integration_mode"]),
        "keycloak_client_id": tenant["keycloak_client_id"],
        "keycloak_realm": tenant["keycloak_realm"],
        "roles": tenant["role_catalog"],
        "permissions": tenant["policy_definition"],
    }


def validate_tenant_payload(payload: dict[str, Any], *, existing_tenant_id: str = "") -> list[str]:
    errors: list[str] = []
    tenant_id = str(payload.get("tenant_id", "")).strip()
    hostname = str(payload.get("primary_hostname", "")).strip()
    backend_origin = str(payload.get("backend_origin", "")).strip()
    integration_mode = str(payload.get("integration_mode", "")).strip()
    identity_mode = str(payload.get("identity_mode", "")).strip()
    role_catalog = payload.get("role_catalog", [])
    secret_refs = payload.get("secret_refs", {})

    if not tenant_id:
        errors.append("tenant_id is required")
    if not hostname:
        errors.append("primary_hostname is required")
    if not backend_origin:
        errors.append("backend_origin is required")
    if integration_mode not in SUPPORTED_INTEGRATION_MODES:
        errors.append("integration_mode must be one of managed_oidc, form_bridge, federated_db")
    if identity_mode not in SUPPORTED_IDENTITY_MODES:
        errors.append("identity_mode must be one of managed, federated_db")
    try:
        derive_backend_parts(backend_origin)
    except ValueError as exc:
        errors.append(str(exc))

    try:
        roles = parse_roles(role_catalog)
    except ValueError as exc:
        errors.append(str(exc))
        roles = []

    try:
        payload["policy_definition"] = normalize_permissions(payload.get("policy_definition"), roles)
    except ValueError as exc:
        errors.append(str(exc))

    if hostname and hostname_exists(hostname, excluding_tenant_id=existing_tenant_id or tenant_id):
        errors.append(f"hostname already assigned: {hostname}")

    parsed = urlparse(backend_origin)
    if parsed.path not in {"", "/"}:
        errors.append("backend_origin must be an origin only, without path")

    if integration_mode == "managed_oidc" and identity_mode != "managed":
        errors.append("managed_oidc tenants must use identity_mode=managed")
    if integration_mode == "federated_db" and identity_mode != "federated_db":
        errors.append("federated_db tenants must use identity_mode=federated_db")

    if not isinstance(secret_refs, dict):
        errors.append("secret_refs must be an object")
    else:
        if integration_mode in {"managed_oidc", "form_bridge"} and "keycloak_client_secret" not in secret_refs:
            errors.append("secret_refs.keycloak_client_secret is required")
        if integration_mode == "federated_db":
            for required in ("federation_adapter", "federated_db_dsn"):
                if required not in secret_refs:
                    errors.append(f"secret_refs.{required} is required for federated_db")

    if payload.get("adapter_mode", "headers") == "translated_token" and "downstream_jwt_secret" not in secret_refs:
        errors.append("secret_refs.downstream_jwt_secret is required for translated_token adapter mode")

    return errors


def build_client_change_summary(integration_mode: str, assessment: dict[str, Any] | None) -> list[str]:
    changes = [
        "Point the protected hostname or DNS record to the ZTAM gateway.",
        "Ensure the application uses the protected hostname for browser redirects.",
        "Backend should trust ZTAM identity headers: x-user-id, x-username, x-user-roles, x-tenant-id.",
    ]
    if integration_mode == "managed_oidc":
        changes.append("Let Keycloak/ZTAM own the login redirect and callback flow.")
    elif integration_mode == "form_bridge":
        changes.append("Keep or adapt the existing login POST path to the ZTAM form-bridge flow.")
    else:
        changes.append("Provide user-table, credential, and adapter compatibility details for DB federation.")

    if assessment:
        if assessment.get("risk_notes"):
            changes.append("Review redirect or cookie behavior identified by the automated assessment.")
        if assessment.get("login_hints"):
            changes.append("Confirm whether existing app login screens should stay in place or be bypassed.")
    return changes


def publish_runtime_bundles() -> dict[str, Any]:
    published_tenants = [tenant for tenant in load_all_tenants() if tenant["status"] == "published"]
    runtime_tenants = [tenant_to_runtime_shape(tenant) for tenant in published_tenants]

    auth_bundle = {
        "version": "v1",
        "generated_at": utc_now(),
        "tenants": [
            {
                "tenant_id": tenant["tenant_id"],
                "display_name": tenant["display_name"],
                "primary_hostname": tenant["primary_hostname"],
                "integration_mode": tenant["integration_mode"],
                "identity_mode": tenant["identity_mode"],
                "adapter_mode": tenant.get("adapter_mode", "headers"),
                "keycloak_realm": tenant["keycloak_realm"],
                "keycloak_client_id": tenant["keycloak_client_id"],
                "status": tenant["status"],
            }
            for tenant in published_tenants
        ],
    }
    routing_bundle = {
        "version": "v1",
        "generated_at": utc_now(),
        "tenants": [
            {
                "tenant_id": tenant["tenant_id"],
                "display_name": tenant["display_name"],
                "primary_hostname": tenant["primary_hostname"],
                "backend_origin": tenant["backend_origin"],
                "login_mode": integration_to_login_mode(tenant["integration_mode"]),
                "backend_host": runtime["backend_host"],
                "backend_port": runtime["backend_port"],
                "backend_tls": runtime["backend_tls"],
                "status": tenant["status"],
            }
            for tenant, runtime in zip(published_tenants, runtime_tenants)
        ],
    }
    policy_bundle = {
        "version": "v1",
        "generated_at": utc_now(),
        "tenants": [
            {
                "tenant_id": tenant["tenant_id"],
                "roles": tenant["policy_definition"],
            }
            for tenant in published_tenants
        ],
    }

    write_json(AUTH_BUNDLE_FILE, auth_bundle)
    write_json(ROUTING_BUNDLE_FILE, routing_bundle)
    write_json(POLICY_BUNDLE_FILE, policy_bundle)

    legacy_policy = {
        tenant["tenant_id"]: {"roles": tenant["policy_definition"]}
        for tenant in published_tenants
    }
    write_json(POLICIES_FILE, legacy_policy)

    content = ENVOY_YAML.read_text(encoding="utf-8")
    routes_blob = "\n".join(render_tenant_vhost(tenant) for tenant in runtime_tenants)
    clusters_blob = "\n".join(render_tenant_cluster(tenant) for tenant in runtime_tenants)
    content = _replace_generated_block(content, ROUTES_BEGIN_MARKER, ROUTES_END_MARKER, routes_blob)
    content = _replace_generated_block(content, CLUSTERS_BEGIN_MARKER, CLUSTERS_END_MARKER, clusters_blob)
    ENVOY_YAML.write_text(content, encoding="utf-8")

    return {
        "published_tenants": len(published_tenants),
        "auth_bundle": str(AUTH_BUNDLE_FILE),
        "routing_bundle": str(ROUTING_BUNDLE_FILE),
        "policy_bundle": str(POLICY_BUNDLE_FILE),
        "envoy_config": str(ENVOY_YAML),
        "policies_file": str(POLICIES_FILE),
    }


class TenantUpsert(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    display_name: str
    primary_hostname: str
    backend_origin: str
    integration_mode: Literal["managed_oidc", "form_bridge", "federated_db"] = "managed_oidc"
    identity_mode: Literal["managed", "federated_db"] = "managed"
    keycloak_realm: str = "test-tenant"
    keycloak_client_id: str
    secret_refs: dict[str, str] = Field(default_factory=dict)
    role_catalog: list[str] = Field(default_factory=lambda: ["admin", "editor", "user", "viewer"])
    policy_definition: dict[str, Any] | None = None
    adapter_mode: Literal["headers", "translated_token"] = "headers"
    client_change_summary: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


class AssessRequest(BaseModel):
    backend_url: str
    candidate_paths: list[str] = Field(default_factory=lambda: ["/", "/login", "/admin", "/dashboard", "/app"])
    timeout: int = 15
    insecure: bool = False


class PublishStatusResponse(BaseModel):
    tenant_id: str
    status: str
    revision: int
    published_at: str | None = None


def legacy_tenant_to_record(raw: dict[str, Any]) -> dict[str, Any]:
    integration_mode = "managed_oidc" if raw.get("login_mode", "keycloak") == "keycloak" else "form_bridge"
    adapter_mode = raw.get("adapter_mode", "headers")
    now = raw.get("created_at") or utc_now()
    policy_definition = normalize_permissions(raw.get("permissions"), parse_roles(raw.get("roles", [])))
    return {
        "tenant_id": raw["name"],
        "display_name": raw.get("display_name", raw["name"]),
        "primary_hostname": raw["hostname"],
        "backend_origin": raw["backend_url"],
        "integration_mode": integration_mode,
        "identity_mode": "federated_db" if raw.get("no_spi") else "managed",
        "keycloak_realm": raw.get("keycloak_realm", "test-tenant"),
        "keycloak_client_id": raw.get("keycloak_client_id", raw["name"]),
        "secret_refs": {
            "keycloak_client_secret": "env:KC_CLIENT_SECRET",
            **({"downstream_jwt_secret": "env:TESTAPP_JWT_SECRET"} if adapter_mode == "translated_token" else {}),
        },
        "role_catalog": parse_roles(raw.get("roles", [])),
        "policy_definition": policy_definition,
        "adapter_mode": adapter_mode,
        "client_change_summary": build_client_change_summary(integration_mode, None),
        "status": "published",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "published_at": now,
        "assessment": None,
        "audit_metadata": {"source": "legacy-import"},
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tenants")
def list_tenants() -> list[dict[str, Any]]:
    return load_all_tenants()


@app.post("/tenants")
def create_tenant(tenant: TenantUpsert) -> dict[str, Any]:
    if load_all_tenants():
        try:
            load_tenant(tenant.tenant_id)
            raise HTTPException(status_code=409, detail=f"tenant already exists: {tenant.tenant_id}")
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    payload = tenant.model_dump()
    payload["policy_definition"] = normalize_permissions(payload.get("policy_definition"), parse_roles(payload["role_catalog"]))
    payload["client_change_summary"] = payload["client_change_summary"] or build_client_change_summary(
        payload["integration_mode"], None
    )
    errors = validate_tenant_payload(payload)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    now = utc_now()
    record = {
        **payload,
        "status": "draft",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "assessment": None,
    }
    return save_tenant(record, "created")


@app.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: str) -> dict[str, Any]:
    return load_tenant(tenant_id)


@app.put("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, tenant: TenantUpsert) -> dict[str, Any]:
    existing = load_tenant(tenant_id)
    payload = tenant.model_dump()
    if tenant_id != payload["tenant_id"]:
        raise HTTPException(status_code=400, detail="tenant_id in path and payload must match")
    payload["policy_definition"] = normalize_permissions(payload.get("policy_definition"), parse_roles(payload["role_catalog"]))
    payload["client_change_summary"] = payload["client_change_summary"] or existing.get("client_change_summary") or build_client_change_summary(
        payload["integration_mode"], existing.get("assessment")
    )
    errors = validate_tenant_payload(payload, existing_tenant_id=tenant_id)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    record = {
        **existing,
        **payload,
        "status": "draft" if existing["status"] == "published" else existing["status"],
        "revision": int(existing["revision"]) + 1,
        "updated_at": utc_now(),
        "published_at": existing.get("published_at"),
    }
    return save_tenant(record, "updated")


@app.post("/tenants/{tenant_id}/assess")
def assess_tenant(tenant_id: str, request: AssessRequest) -> dict[str, Any]:
    tenant = load_tenant(tenant_id)
    report = assess_backend(
        request.backend_url,
        candidate_paths=request.candidate_paths,
        insecure=request.insecure,
        timeout=request.timeout,
    )
    recommended_mode = {
        "form": "form_bridge",
        "keycloak": "managed_oidc",
    }[report["recommended_login_mode"]]
    tenant["assessment"] = report
    tenant["integration_mode"] = recommended_mode if tenant["integration_mode"] == "managed_oidc" else tenant["integration_mode"]
    tenant["client_change_summary"] = build_client_change_summary(tenant["integration_mode"], report)
    tenant["revision"] = int(tenant["revision"]) + 1
    tenant["updated_at"] = utc_now()
    save_tenant(tenant, "assessed")
    return {
        "tenant_id": tenant_id,
        "report": report,
        "recommended_integration_mode": recommended_mode,
        "client_change_summary": tenant["client_change_summary"],
    }


@app.post("/tenants/{tenant_id}/validate")
def validate_tenant(tenant_id: str) -> dict[str, Any]:
    tenant = load_tenant(tenant_id)
    errors = validate_tenant_payload(tenant, existing_tenant_id=tenant_id)
    if not tenant.get("assessment"):
        errors.append("assessment is required before validation")
    if tenant.get("integration_mode") == "federated_db":
        assessment = tenant.get("assessment") or {}
        if "Provide user-table" not in " ".join(tenant.get("client_change_summary", [])):
            errors.append("federated_db tenants must document DB adapter requirements")
        if assessment.get("integration_verdict") == "blocked":
            errors.append("federated_db tenant backend assessment is blocked")

    if errors:
        tenant["status"] = "draft"
        tenant["revision"] = int(tenant["revision"]) + 1
        tenant["updated_at"] = utc_now()
        save_tenant(tenant, "validation_failed")
        raise HTTPException(status_code=400, detail=errors)

    tenant["status"] = "validated"
    tenant["revision"] = int(tenant["revision"]) + 1
    tenant["updated_at"] = utc_now()
    save_tenant(tenant, "validated")
    return {
        "tenant_id": tenant_id,
        "status": tenant["status"],
        "revision": tenant["revision"],
        "client_change_summary": tenant.get("client_change_summary", []),
    }


@app.post("/tenants/{tenant_id}/publish")
def publish_tenant(tenant_id: str) -> dict[str, Any]:
    tenant = load_tenant(tenant_id)
    if tenant["status"] != "validated":
        raise HTTPException(status_code=400, detail="tenant must be validated before publish")
    tenant["status"] = "published"
    tenant["revision"] = int(tenant["revision"]) + 1
    tenant["updated_at"] = utc_now()
    tenant["published_at"] = tenant["updated_at"]
    save_tenant(tenant, "published")
    bundle_result = publish_runtime_bundles()
    return {
        "tenant_id": tenant_id,
        "status": tenant["status"],
        "revision": tenant["revision"],
        "published_at": tenant["published_at"],
        "bundles": bundle_result,
    }


@app.post("/tenants/{tenant_id}/disable")
def disable_tenant(tenant_id: str) -> dict[str, Any]:
    tenant = load_tenant(tenant_id)
    tenant["status"] = "disabled"
    tenant["revision"] = int(tenant["revision"]) + 1
    tenant["updated_at"] = utc_now()
    save_tenant(tenant, "disabled")
    bundle_result = publish_runtime_bundles()
    return {
        "tenant_id": tenant_id,
        "status": tenant["status"],
        "revision": tenant["revision"],
        "bundles": bundle_result,
    }


@app.get("/tenants/{tenant_id}/revisions")
def tenant_revisions(tenant_id: str) -> list[dict[str, Any]]:
    load_tenant(tenant_id)
    return load_revisions(tenant_id)


@app.get("/tenants/{tenant_id}/publish-status", response_model=PublishStatusResponse)
def tenant_publish_status(tenant_id: str) -> PublishStatusResponse:
    tenant = load_tenant(tenant_id)
    return PublishStatusResponse(
        tenant_id=tenant_id,
        status=tenant["status"],
        revision=int(tenant["revision"]),
        published_at=tenant.get("published_at"),
    )


@app.post("/tenants/import-legacy")
def import_legacy_tenants() -> dict[str, Any]:
    imported = 0
    for config_path in sorted(TENANTS_DIR.glob("*/config.json")):
        if config_path.parent.name == "_template":
            continue
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        record = legacy_tenant_to_record(raw)
        try:
            load_tenant(record["tenant_id"])
            continue
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        save_tenant(record, "legacy_imported")
        imported += 1
    bundle_result = publish_runtime_bundles()
    return {"imported": imported, "bundles": bundle_result}
