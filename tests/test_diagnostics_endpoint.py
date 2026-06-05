"""Tests for the version + diagnostics observability endpoints (Part 5)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.version import BACKEND_VERSION


def _client() -> TestClient:
    return TestClient(create_app())


def test_health_includes_backend_version() -> None:
    response = _client().get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == BACKEND_VERSION


def test_version_endpoint_returns_backend_version() -> None:
    response = _client().get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": BACKEND_VERSION}


def test_diagnostics_aggregates_real_status() -> None:
    response = _client().get("/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["backend_version"] == BACKEND_VERSION
    # Database reachability is a real probe, not a fabricated literal.
    assert body["database"]["ok"] is True
    assert body["database"]["error"] is None
    # Health snapshot is present and the last_error key is exposed.
    assert "health" in body
    assert "last_error" in body
