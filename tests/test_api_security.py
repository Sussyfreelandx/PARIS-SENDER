"""Tests for opt-in API authentication and rate limiting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app, issue_access_token
from backend.repositories import LedgerRepository


SECRET = "unit-test-jwt-secret"


def test_auth_is_disabled_by_default() -> None:
    client = TestClient(create_app(repository=LedgerRepository(":memory:")))

    response = client.post("/campaigns", json={"name": "Open"})

    assert response.status_code == 201


def test_auth_requires_bearer_token_when_enabled() -> None:
    client = TestClient(create_app(repository=LedgerRepository(":memory:"), enable_auth=True, jwt_secret=SECRET))

    response = client.get("/campaigns/1")

    assert response.status_code == 401


def test_rbac_allows_admin_and_denies_viewer_writes() -> None:
    client = TestClient(create_app(repository=LedgerRepository(":memory:"), enable_auth=True, jwt_secret=SECRET))
    viewer = issue_access_token("viewer", ["viewer"], secret=SECRET)
    admin = issue_access_token("admin", ["admin"], secret=SECRET)

    denied = client.post("/campaigns", json={"name": "Denied"}, headers={"Authorization": "Bearer " + viewer})
    allowed = client.post("/campaigns", json={"name": "Allowed"}, headers={"Authorization": "Bearer " + admin})

    assert denied.status_code == 403
    assert allowed.status_code == 201


def test_rate_limit_is_opt_in_and_per_endpoint() -> None:
    client = TestClient(create_app(repository=LedgerRepository(":memory:"), enable_rate_limit=True, rate_limit_requests=2))

    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 429
