"""API tests for Phase 8 health monitor endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import ComponentHealth, Domain, HealthServer, HealthStatus
from backend.repositories import DomainRepository, LedgerRepository, WarmupRepository
from backend.services import DomainService, HealthMonitorService, WarmupService


def _probe(status: HealthStatus):
    def check(server: HealthServer, *, now):
        return ComponentHealth(server.id, server.kind, status, "checked", {"host": server.host}, now)

    return check


def _client() -> TestClient:
    domain_service = DomainService(DomainRepository(":memory:"))
    domain_service.repository.create(Domain(name="api.example", dkim_verified=True, spf_verified=True, dmarc_verified=True))
    health = HealthMonitorService(
        ledger=LedgerRepository(":memory:"),
        domain_service=domain_service,
        warmup_service=WarmupService(WarmupRepository(":memory:")),
        servers=[{"id": "smtp-1", "host": "smtp.local", "kind": "smtp"}],
        smtp_probe=_probe(HealthStatus.OK),
        clock=lambda: datetime(2025, 1, 1, 12, tzinfo=timezone.utc),
    )
    return TestClient(create_app(repository=LedgerRepository(":memory:"), domain_service=domain_service, health_service=health))


def test_health_status_endpoint_returns_aggregate_summary() -> None:
    client = _client()

    response = client.get("/health/status")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == HealthStatus.OK.value
    assert data["queue_depth"]["total"] == 0
    assert data["throughput"]["sent"] == 0
    assert data["servers"][0]["server_id"] == "smtp-1"
    assert any(component["name"] == "Non-SMTP delivery path" for component in data["components"])


def test_health_domain_endpoint_returns_detail_and_404() -> None:
    client = _client()

    ok = client.get("/health/domain/api.example")
    missing = client.get("/health/domain/missing.example")

    assert ok.status_code == 200
    assert ok.json()["health_score"] == 100
    assert len(ok.json()["records"]) == 3
    assert missing.status_code == 404


def test_health_server_endpoint_returns_detail_and_404() -> None:
    client = _client()

    ok = client.get("/health/server/smtp-1")
    missing = client.get("/health/server/nope")

    assert ok.status_code == 200
    assert ok.json()["status"] == HealthStatus.OK.value
    assert ok.json()["host"] == "smtp.local"
    assert missing.status_code == 404
