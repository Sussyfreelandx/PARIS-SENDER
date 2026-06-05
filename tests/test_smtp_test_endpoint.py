"""API tests for the SMTP connection-test endpoint (/smtp/test)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app


class _OkClient:
    """Minimal SMTP client stand-in that accepts the connection."""

    def __init__(self) -> None:
        self.quit_called = False

    def noop(self):
        return (250, b"OK")

    def quit(self):
        self.quit_called = True


def _factory_ok(config, context):
    return _OkClient()


def _factory_login_fail(config, context):
    raise RuntimeError("535 Authentication failed")


def _client(factory):
    app = create_app(smtp_test_factory=factory, enforce_verified_domains=False)
    return TestClient(app)


def test_smtp_test_success_returns_ok():
    client = _client(_factory_ok)
    resp = client.post(
        "/smtp/test",
        json={"host": "smtp.example.com", "port": 587, "username": "u", "password": "p"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "success" in (body["detail"] or "").lower()


def test_smtp_test_failure_reports_error():
    client = _client(_factory_login_fail)
    resp = client.post(
        "/smtp/test",
        json={"host": "smtp.example.com", "port": 587, "username": "u", "password": "bad"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "Authentication failed" in (body["detail"] or "")


def test_smtp_test_requires_host():
    client = _client(_factory_ok)
    resp = client.post("/smtp/test", json={"port": 587})
    assert resp.status_code == 422
