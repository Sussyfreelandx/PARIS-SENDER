from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import Status
from backend.repositories import LedgerRepository
from backend.services import DeliverabilityService, DeliveryProvider, DeliveryResult, NonSmtpDeliveryProvider, OutboundMessage
from tests.conftest import FakeResolver
from backend.services.domain import DomainService
from backend.repositories import DomainRepository, LogRepository
from backend.services.logging_service import LoggingService


class RecordingProvider(DeliveryProvider):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.messages: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> DeliveryResult:
        self.messages.append(message)
        return DeliveryResult(True, provider_message_id=f"{self.provider_id}-{len(self.messages)}")


def _client(repo: LedgerRepository, smtp: DeliveryProvider, non_smtp: DeliveryProvider, *, threshold: int = 70) -> TestClient:
    resolver = FakeResolver()
    domains = DomainService(DomainRepository(":memory:"), resolver=resolver)
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        provider=smtp,
        non_smtp_provider=non_smtp,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, threshold=threshold, logger=logger),
        logging_service=logger,
        enforce_verified_domains=False,
    )
    return TestClient(app)


def _campaign(client: TestClient) -> int:
    return client.post("/campaigns", json={"name": "Non-SMTP"}).json()["id"]


def test_non_smtp_flag_uses_non_smtp_provider_and_records_sent() -> None:
    repo = LedgerRepository(":memory:")
    smtp = RecordingProvider("smtp")
    non_smtp = RecordingProvider("non-smtp")
    client = _client(repo, smtp, non_smtp)
    campaign_id = _campaign(client)

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Requested update",
            "sender": "sender@example.com",
            "non_smtp_delivery": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery_channel"] == "non_smtp"
    assert len(non_smtp.messages) == 1
    assert smtp.messages == []
    assert repo.recipient_status_rollups(campaign_id)[Status.SENT] == 1


def test_default_send_uses_smtp_provider_and_reports_channel() -> None:
    repo = LedgerRepository(":memory:")
    smtp = RecordingProvider("smtp")
    non_smtp = RecordingProvider("non-smtp")
    client = _client(repo, smtp, non_smtp)
    campaign_id = _campaign(client)

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Requested update", "sender": "sender@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["delivery_channel"] == "smtp"
    assert len(smtp.messages) == 1
    assert non_smtp.messages == []


def test_deliverability_gate_blocks_non_smtp_before_provider_dispatch() -> None:
    repo = LedgerRepository(":memory:")
    smtp = RecordingProvider("smtp")
    non_smtp = RecordingProvider("non-smtp")
    client = _client(repo, smtp, non_smtp, threshold=95)
    campaign_id = _campaign(client)

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["bad-address", "bad-address"],
            "subject": "FREE winner",
            "content": "urgent lottery cash offer",
            "sender": "sender@example.com",
            "non_smtp_delivery": True,
        },
    )

    assert response.status_code == 400
    assert "deliverability score" in response.json()["detail"]
    assert smtp.messages == []
    assert non_smtp.messages == []


def test_non_smtp_provider_builds_shared_mime_before_transport() -> None:
    captured: list[OutboundMessage] = []

    def transport(message: OutboundMessage) -> DeliveryResult:
        captured.append(message)
        return DeliveryResult(True, provider_message_id="non-smtp-id")

    result = NonSmtpDeliveryProvider(transport).send(OutboundMessage("s@example.com", "r@example.com", "Hi", "Body"))

    assert result.success is True
    assert captured[0].metadata is not None
    assert captured[0].metadata["mime_message"]["To"] == "r@example.com"
