"""API tests for centralized backend logging endpoints and integrations."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import LogComponent
from backend.repositories import LedgerRepository, LogRepository, WarmupRepository
from backend.services import DeliveryProvider, DeliveryResult, LoggingService, WarmupService


class FakeProvider(DeliveryProvider):
    """Delivery provider test double."""

    def send(self, message):
        """Return a successful delivery result without network calls."""
        return DeliveryResult(True, provider_message_id="log-api-id")


def _client() -> tuple[TestClient, LoggingService]:
    logger = LoggingService(LogRepository(":memory:"))
    warmup = WarmupService(WarmupRepository(":memory:"))
    app = create_app(
        repository=LedgerRepository(":memory:"),
        provider=FakeProvider(),
        non_smtp_provider=FakeProvider(),
        warmup_service=warmup,
        logging_service=logger,
        enforce_verified_domains=False,
    )
    return TestClient(app), logger


def _campaign(client: TestClient) -> int:
    return client.post("/campaigns", json={"name": "Logging"}).json()["id"]


def test_logs_endpoint_filters_and_summary() -> None:
    client, logger = _client()
    logger.info(LogComponent.API, "api up", route="/health")
    logger.error(LogComponent.HEALTH, "health red", server="smtp-1")

    filtered = client.get("/logs", params={"severity": "ERROR", "component": "HEALTH"})
    summary = client.get("/logs/summary")

    assert filtered.status_code == 200
    assert filtered.json()["logs"][0]["message"] == "health red"
    assert filtered.json()["logs"][0]["context"] == {"server": "smtp-1"}
    assert summary.status_code == 200
    assert summary.json()["total"] == 2
    assert summary.json()["by_component"]["API"] == 1


def test_campaign_send_emits_centralized_logs_for_non_smtp_path() -> None:
    client, _ = _client()
    campaign_id = _campaign(client)
    client.post(
        "/warmup/domains",
        json={"domain": "acme.com", "daily_limit": 5, "max_per_batch": 5, "max_per_hour": 5, "ramp_start_limit": 5, "ramp_days": 1},
    )

    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com", "b@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@acme.com",
            "non_smtp_delivery": True,
        },
    )

    assert sent.status_code == 200
    logs = client.get("/logs", params={"limit": 50}).json()["logs"]
    components = {entry["component"] for entry in logs}
    assert {"CAMPAIGN", "DELIVERY", "DELIVERABILITY", "WARMUP"}.issubset(components)
    assert any(entry["component"] == "CAMPAIGN" and entry["context"].get("non_smtp_delivery") is True for entry in logs)
    assert any(entry["component"] == "DELIVERY" and entry["context"].get("sent") == 2 for entry in logs)


def test_autograb_and_health_events_are_logged() -> None:
    client, _ = _client()

    preview = client.post("/compose/preview", json={"template": "Hello {{ first_name }}", "email": "ada@example.com"})
    health = client.get("/health/status")

    assert preview.status_code == 200
    assert health.status_code == 200
    logs = client.get("/logs", params={"limit": 20}).json()["logs"]
    assert any(entry["component"] == "AUTOGRAB" for entry in logs)
    assert any(entry["component"] == "HEALTH" for entry in logs)
