"""Tests for the /sessions/last endpoint."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# ── Helper: fake JWT claims ──────────────────────────────────────────────────

FAKE_CLAIMS = {
    "sub": "user-uuid-123",
    "email": "alice@example.com",
    "preferred_username": "alice",
    "role": "admin",
}

FAKE_SESSION = {
    "id": "session-abc",
    "username": "alice",
    "ipAddress": "10.0.0.1",
    "start": 1700000000000,
    "lastAccess": 1700003600000,
    "clients": {"test-app": "test-app"},
}


def _mock_kc_response(json_data, status_code=200):
    """Create a mock httpx response (non-async json())."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ── Tests ────────────────────────────────────────────────────────────────────

def test_last_session_missing_token():
    """GET /sessions/last without Authorization header returns 401."""
    resp = client.get("/sessions/last")
    assert resp.status_code == 401
    assert "missing token" in resp.json()["error"]


def test_last_session_invalid_token():
    """GET /sessions/last with an invalid JWT returns 403."""
    with patch("main.get_jwks", new_callable=AsyncMock, return_value={"keys": []}):
        resp = client.get(
            "/sessions/last",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
    assert resp.status_code == 403
    assert "invalid or expired token" in resp.json()["error"]


@patch("main.get_admin_token", new_callable=AsyncMock)
@patch("main.jwt.decode")
@patch("main.get_jwks", new_callable=AsyncMock, return_value={"keys": []})
def test_last_session_no_sessions(mock_jwks, mock_decode, mock_admin):
    """Returns 404 when the user has no active sessions."""
    mock_decode.return_value = FAKE_CLAIMS
    mock_admin.return_value = "admin-token"

    mock_resp = _mock_kc_response([])
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        resp = client.get(
            "/sessions/last",
            headers={"Authorization": "Bearer valid.jwt.token"},
        )

    assert resp.status_code == 404
    assert "no active sessions found" in resp.json()["error"]


@patch("main.get_admin_token", new_callable=AsyncMock)
@patch("main.jwt.decode")
@patch("main.get_jwks", new_callable=AsyncMock, return_value={"keys": []})
def test_last_session_success(mock_jwks, mock_decode, mock_admin):
    """Returns the most recent session when sessions exist."""
    mock_decode.return_value = FAKE_CLAIMS
    mock_admin.return_value = "admin-token"

    older_session = {**FAKE_SESSION, "id": "session-old", "lastAccess": 1700000000000}
    newer_session = {**FAKE_SESSION, "id": "session-new", "lastAccess": 1700099999000}

    mock_resp = _mock_kc_response([older_session, newer_session])
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        resp = client.get(
            "/sessions/last",
            headers={"Authorization": "Bearer valid.jwt.token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "session-new"
    assert data["username"] == "alice"
    assert data["ip_address"] == "10.0.0.1"
    assert data["last_access"] == 1700099999000
