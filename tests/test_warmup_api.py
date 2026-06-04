"""API tests for warmup configuration, status, overrides, and send gate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.repositories import DomainRepository, LedgerRepository, WarmupRepository
from backend.services import DeliveryProvider, DeliveryResult, DomainService, WarmupService


class FakeProvider(DeliveryProvider):
    """Delivery provider test double."""

    def send(self, message):
        """Return a successful delivery result."""
        return DeliveryResult(True, provider_message_id="api-id")


class FakeResolver:
    """DNS resolver test double."""

    def __init__(self, records=None):
        self.records = records or {}

    def resolve_txt(self, host):
        """Resolve TXT records from an in-memory map."""
        return self.records.get(host, [])


def _client():
    domain_service = DomainService(DomainRepository(":memory:"), resolver=FakeResolver())
    warmup_service = WarmupService(WarmupRepository(":memory:"))
    app = create_app(
        repository=LedgerRepository(":memory:"),
        provider=FakeProvider(),
        non_smtp_provider=FakeProvider(),
        domain_service=domain_service,
        warmup_service=warmup_service,
    )
    return TestClient(app), domain_service, warmup_service


def _verify_domain(service: DomainService, name: str = "acme.com") -> None:
    domain = service.add_domain(name)
    records = {record.host: [record.value] for record in service.required_records(domain)}
    service.resolver = FakeResolver(records)
    service.verify_domain(domain.id)


def _campaign(client: TestClient) -> int:
    return client.post("/campaigns", json={"name": "Warmup"}).json()["id"]


def test_configure_warmup_and_get_status() -> None:
    client, _, _ = _client()

    response = client.post(
        "/warmup/domains",
        json={"domain": "Acme.com", "daily_limit": 10, "max_per_batch": 3, "max_per_hour": 4, "ramp_start_limit": 2, "ramp_days": 3},
    )

    assert response.status_code == 201
    assert response.json()["domain"] == "acme.com"
    listing = client.get("/warmup/domains")
    assert listing.status_code == 200
    assert listing.json()["domains"][0]["domain"] == "acme.com"
    status = client.get("/warmup/domains/acme.com/status")
    assert status.status_code == 200
    assert status.json()["daily_limit"] == 2


def test_send_enforces_warmup_limit_and_logs_events() -> None:
    client, domains, warmup = _client()
    _verify_domain(domains)
    campaign_id = _campaign(client)
    client.post(
        "/warmup/domains",
        json={"domain": "acme.com", "daily_limit": 2, "max_per_batch": 2, "max_per_hour": 2, "ramp_start_limit": 2, "ramp_days": 1},
    )

    blocked = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com", "c@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com", "non_smtp_delivery": True},
    )
    assert blocked.status_code == 400
    assert "warmup limit" in blocked.json()["detail"]

    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com", "non_smtp_delivery": True},
    )
    assert sent.status_code == 200
    assert sent.json()["sent"] == 2
    events = warmup.events("acme.com")
    assert [event["event_type"] for event in events] == ["WarmupExecuted", "WarmupScheduled"]


def test_override_endpoint_requires_authorization_and_raises_limit() -> None:
    client, domains, _ = _client()
    _verify_domain(domains)
    client.post(
        "/warmup/domains",
        json={"domain": "acme.com", "daily_limit": 1, "max_per_batch": 1, "max_per_hour": 1, "ramp_start_limit": 1, "ramp_days": 1},
    )

    denied = client.post("/warmup/domains/acme.com/override", json={"authorized": False, "daily_limit": 5})
    assert denied.status_code == 403

    raised = client.post("/warmup/domains/acme.com/override", json={"authorized": True, "daily_limit": 5, "max_per_batch": 5, "max_per_hour": 5})
    assert raised.status_code == 200
    assert raised.json()["config"]["daily_limit"] == 5

    campaign_id = _campaign(client)
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com"},
    )
    assert sent.status_code == 200


def test_non_warmup_domain_sends_normally_regression() -> None:
    client, domains, _ = _client()
    _verify_domain(domains, "plain.com")
    campaign_id = _campaign(client)

    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com", "c@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@plain.com"},
    )

    assert sent.status_code == 200
    assert sent.json()["sent"] == 3
