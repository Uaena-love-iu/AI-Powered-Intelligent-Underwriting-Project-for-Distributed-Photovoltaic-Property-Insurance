from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator

import pytest
from app.main import app
from app.security import AuthRepository, LastActiveAdmin
from fastapi.testclient import TestClient


@pytest.fixture
def authenticated_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PV_AUTH_ENABLED", "true")
    monkeypatch.setenv("PV_ADMIN_USERNAME", "admin-user")
    monkeypatch.setenv("PV_ADMIN_PASSWORD", "a-long-demo-password-123")
    client = TestClient(app)
    with client:
        yield client


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_auth_disabled_by_default_preserves_local_demo_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/config").json()["enabled"] is False
        assert client.get("/api/v1/cases").status_code == 200


def test_login_stores_only_hashed_session_token(
    authenticated_client: TestClient,
) -> None:
    token = _login(
        authenticated_client,
        "admin-user",
        "a-long-demo-password-123",
    )
    response = authenticated_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "admin-user", "role": "admin"}
    with sqlite3.connect(os.environ["PV_CASE_DB_PATH"]) as connection:
        stored = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()
    assert stored is not None
    assert token not in stored[0]


def test_roles_limit_review_and_user_management(
    authenticated_client: TestClient,
) -> None:
    admin_token = _login(
        authenticated_client,
        "admin-user",
        "a-long-demo-password-123",
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    created = authenticated_client.post(
        "/api/v1/auth/users",
        headers=admin_headers,
        json={"username": "viewer-user", "password": "viewer-password-123", "role": "viewer"},
    )
    assert created.status_code == 201
    viewer_token = _login(
        authenticated_client,
        "viewer-user",
        "viewer-password-123",
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    assert authenticated_client.get("/api/v1/cases", headers=viewer_headers).status_code == 200
    assert authenticated_client.post(
        "/api/v1/cases/missing/review",
        headers=viewer_headers,
        json={"final_decision": "accept", "reviewer_name": "viewer-user", "comment": ""},
    ).status_code == 403
    assert authenticated_client.get("/api/v1/auth/users", headers=viewer_headers).status_code == 403
    assert authenticated_client.post(
        "/api/v1/auth/logout",
        headers=viewer_headers,
    ).status_code == 204

    assert authenticated_client.get("/api/v1/auth/me", headers=viewer_headers).status_code == 401


def test_admin_cannot_deactivate_last_admin_and_password_reset_revokes_sessions(
    authenticated_client: TestClient,
) -> None:
    admin_token = _login(
        authenticated_client,
        "admin-user",
        "a-long-demo-password-123",
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    with pytest.raises(LastActiveAdmin):
        AuthRepository().deactivate_user("admin-user")
    self_deactivation = authenticated_client.post(
        "/api/v1/auth/users/admin-user/deactivate",
        headers=headers,
    )
    assert self_deactivation.status_code == 400

    reset = authenticated_client.put(
        "/api/v1/auth/users/admin-user/password",
        headers=headers,
        json={"password": "a-new-demo-password-456"},
    )
    assert reset.status_code == 200
    assert authenticated_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert authenticated_client.post(
        "/api/v1/auth/login",
        json={"username": "admin-user", "password": "a-long-demo-password-123"},
    ).status_code == 401
    assert authenticated_client.post(
        "/api/v1/auth/login",
        json={"username": "admin-user", "password": "a-new-demo-password-456"},
    ).status_code == 200
