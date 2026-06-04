"""API tests for deliverability scoring endpoints and send gate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.repositories import DomainRepository, LedgerRepository
from backend.services import DeliveryProvider, DeliveryResult, DomainService


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


def _client(*, min_deliverability_score=70):
    domain_service = DomainService(DomainRepository(":memory:"), resolver=FakeResolver())
    app = create_app(
        repository=LedgerRepository(":memory:"),
        provider=FakeProvider(),
        domain_service=domain_service,
        min_deliverability_score=min_deliverability_score,
    )
    return TestClient(app), domain_service


def _verify_domain(service: DomainService, name: str = "acme.com") -> None:
    domain = service.add_domain(name)
    records = {record.host: [record.value] for record in service.required_records(domain)}
    service.resolver = FakeResolver(records)
    service.verify_domain(domain.id)


def test_get_campaign_score_happy_path_and_404() -> None:
    client, service = _client()
    _verify_domain(service)
    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]

    response = client.get(f"/campaigns/{campaign_id}/score", params={"content": "Hello", "sender": "sender@acme.com"})

    assert response.status_code == 200
    body = response.json()
    assert 0 <= body["score"] <= 100
    assert body["passed"] is True
    assert len(body["components"]) == 5
    assert client.get("/campaigns/999/score").status_code == 404


def test_predict_campaign_happy_path_and_404() -> None:
    client, service = _client()
    _verify_domain(service)
    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/predict",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com"},
    )

    assert response.status_code == 200
    assert 0 <= response.json()["score"] <= 100
    assert client.post(
        "/campaigns/999/predict",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com"},
    ).status_code == 404


def test_send_blocked_when_score_below_threshold() -> None:
    client, service = _client(min_deliverability_score=95)
    _verify_domain(service)
    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "FREE winner urgent",
            "content": "FREE winner urgent act now click here buy now lottery cash discount offer",
            "sender": "sender@acme.com",
            "non_smtp_delivery": True,
        },
    )

    assert response.status_code == 400
    assert "deliverability score" in response.json()["detail"]


def test_send_allowed_when_score_above_threshold() -> None:
    client, service = _client()
    _verify_domain(service)
    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Body", "sender": "sender@acme.com"},
    )

    assert response.status_code == 200
    assert response.json()["sent"] == 1
