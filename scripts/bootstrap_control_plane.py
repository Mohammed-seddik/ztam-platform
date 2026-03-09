#!/usr/bin/env python3
"""Bootstrap the control-plane SQLite database from legacy tenant configs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tenant_manager import normalize_tenant_config


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Bootstrap the ZTAM control-plane DB from legacy tenant configs")
    parser.add_argument(
        "--tenants-dir",
        default=str(repo_root / "tenants"),
        help="Path to legacy tenant config directory",
    )
    parser.add_argument(
        "--db-path",
        default=str(repo_root / "build-verify" / "control-plane.db"),
        help="SQLite database path used by the control-plane service",
    )
    return parser.parse_args()


def ensure_schema(conn: sqlite3.Connection) -> None:
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


def main() -> int:
    args = parse_args()
    tenants_dir = Path(args.tenants_dir)
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    imported = 0
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        for config_path in sorted(tenants_dir.glob("*/config.json")):
            if config_path.parent.name == "_template":
                continue
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            normalized = normalize_tenant_config(raw, config_path)
            tenant_id = normalized["name"]
            existing = conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
            if existing:
                continue

            integration_mode = "managed_oidc" if normalized["login_mode"] == "keycloak" else "form_bridge"
            adapter_mode = normalized.get("adapter_mode", "headers")
            now = normalized["created_at"] or utc_now()
            payload = {
                "tenant_id": tenant_id,
                "display_name": normalized["display_name"],
                "primary_hostname": normalized["hostname"],
                "backend_origin": normalized["backend_url"],
                "integration_mode": integration_mode,
                "identity_mode": "federated_db" if normalized["no_spi"] else "managed",
                "keycloak_realm": normalized["keycloak_realm"],
                "keycloak_client_id": normalized["keycloak_client_id"],
                "secret_refs": {
                    "keycloak_client_secret": "env:KC_CLIENT_SECRET",
                    **({"downstream_jwt_secret": "env:TESTAPP_JWT_SECRET"} if adapter_mode == "translated_token" else {}),
                },
                "role_catalog": normalized["roles"],
                "policy_definition": normalized["permissions"],
                "adapter_mode": adapter_mode,
                "client_change_summary": [
                    "Point the protected hostname or DNS record to the ZTAM gateway.",
                    "Ensure the application uses the protected hostname for browser redirects.",
                    "Backend should trust ZTAM identity headers: x-user-id, x-username, x-user-roles, x-tenant-id.",
                ],
                "status": "published",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "published_at": now,
                "assessment": None,
                "audit_metadata": {"source": "legacy-bootstrap"},
            }
            payload_json = json.dumps(payload)
            conn.execute("INSERT INTO tenants (tenant_id, payload) VALUES (?, ?)", (tenant_id, payload_json))
            conn.execute(
                """
                INSERT INTO tenant_revisions (tenant_id, revision, action, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, 1, "legacy_bootstrap", payload_json, utc_now()),
            )
            imported += 1

    print(f"Imported {imported} tenant(s) into {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
