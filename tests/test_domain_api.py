"""API tests for domain management and compose endpoints (Phase 2/4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.repositories import DomainRepository, LedgerRepository
from backend.services import DeliveryProvider, DeliveryResult, DomainService


class FakeProvider(DeliveryProvider):
    def send(self, message):
        return DeliveryResult(True, provider_message_id="api-id")


class FakeResolver:
    def __init__(self, records=None):
        self.records = records or {}

    def resolve_txt(self, host):
        return self.records.get(host, [])


def _client(resolver=None):
    domain_service = DomainService(DomainRepository(":memory:"), resolver=resolver or FakeResolver())
    app = create_app(
        repository=LedgerRepository(":memory:"),
        provider=FakeProvider(),
        domain_service=domain_service,
    )
    return TestClient(app), domain_service


def test_compose_preview_and_analyze():
    client, _ = _client()
    preview = client.post("/compose/preview", json={"template": "Hi [firstname]", "email": "john.doe@acme.com"})
    assert preview.status_code == 200
    assert "John" in preview.json()["rendered"]

    analyze = client.post("/compose/analyze", json={"content": "FREE money [bogus]", "html": False})
    assert analyze.status_code == 200
    body = analyze.json()
    assert body["spam_score"] >= 1
    assert "bogus" in body["unknown_placeholders"]


def test_domain_lifecycle_endpoints():
    client, service = _client()

    created = client.post("/domains", json={"name": "acme.com"})
    assert created.status_code == 201
    domain = created.json()
    domain_id = domain["id"]
    assert domain["status"] == "PENDING"
    assert len(domain["records"]) == 3

    listing = client.get("/domains")
    assert listing.status_code == 200
    assert len(listing.json()["domains"]) == 1

    # invalid name rejected
    assert client.post("/domains", json={"name": "nope"}).status_code == 400
    # duplicate rejected
    assert client.post("/domains", json={"name": "acme.com"}).status_code == 400

    # verification fails with no DNS published
    verified = client.post(f"/domains/{domain_id}/verify")
    assert verified.status_code == 200
    assert verified.json()["status"] == "FAILED"

    # publish records then verify succeeds
    records = {r["host"]: [r["value"]] for r in domain["records"]}
    service.resolver = FakeResolver(records)
    verified = client.post(f"/domains/{domain_id}/verify")
    assert verified.json()["status"] == "VERIFIED"
    assert verified.json()["health_score"] == 100

    history = client.get(f"/domains/{domain_id}/history")
    assert history.status_code == 200
    assert len(history.json()["history"]) >= 2

    # dmarc + dkim mutation
    assert client.patch(f"/domains/{domain_id}/dmarc", json={"policy": "reject"}).json()["dmarc_policy"] == "reject"
    assert client.post(f"/domains/{domain_id}/dkim/rotate").status_code == 200

    assert client.delete(f"/domains/{domain_id}").json()["deleted"] is True
    assert client.get(f"/domains/{domain_id}").status_code == 404


def test_send_blocked_for_unverified_domain():
    records_resolver = FakeResolver()
    client, service = _client(resolver=records_resolver)
    # register sender domain but do not verify
    service.add_domain("acme.com")

    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]
    blocked = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@x.com"], "subject": "Hi", "content": "Body", "sender": "boss@acme.com"},
    )
    assert blocked.status_code == 400
    assert "not verified" in blocked.json()["detail"]


def test_send_allowed_for_verified_domain():
    client, service = _client()
    domain = service.add_domain("acme.com")
    records = {r.host: [r.value] for r in service.required_records(domain)}
    service.resolver = FakeResolver(records)
    service.verify_domain(domain.id)

    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@x.com"], "subject": "Hi", "content": "Body", "sender": "boss@acme.com"},
    )
    assert sent.status_code == 200
    assert sent.json()["sent"] == 1


def test_send_allowed_for_unmanaged_domain():
    # Backward compatible: sending from a domain not under management is allowed.
    client, _ = _client()
    campaign_id = client.post("/campaigns", json={"name": "C"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@x.com"], "subject": "Hi", "content": "Body", "sender": "boss@unmanaged.com"},
    )
    assert sent.status_code == 200
    assert sent.json()["sent"] == 1
