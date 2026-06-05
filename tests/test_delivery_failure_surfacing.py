"""Part 2 audit: delivery failures must expose the real provider reason and retry.

These tests pin the contract that a non-SMTP (or SMTP) send failure surfaces the
exact provider error end-to-end -- in the API response, in the ledger events, and
via the per-message observability endpoint -- and that retry uses exponential
backoff without ever faking success.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import Status
from backend.repositories import DomainRepository, LedgerRepository, LogRepository
from backend.services import DeliverabilityService, DeliveryProvider, DeliveryResult, OutboundMessage
from backend.services.delivery import DeliveryService
from backend.services.domain import DomainService
from backend.services.logging_service import LoggingService
from tests.conftest import FakeResolver


class FailingProvider(DeliveryProvider):
    """Provider that always fails with a specific, real-looking reason."""

    def __init__(self, error: str) -> None:
        self.error = error
        self.calls = 0

    def send(self, message: OutboundMessage) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(success=False, error=self.error)


class FlakyProvider(DeliveryProvider):
    """Fails the first ``fail_times`` calls, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def send(self, message: OutboundMessage) -> DeliveryResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return DeliveryResult(success=False, error=f"temporary failure {self.calls}")
        return DeliveryResult(success=True, provider_message_id=f"ok-{self.calls}")


def _client(repo: LedgerRepository, provider: DeliveryProvider) -> TestClient:
    domains = DomainService(DomainRepository(":memory:"), resolver=FakeResolver())
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        provider=provider,
        non_smtp_provider=provider,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, threshold=0, logger=logger),
        logging_service=logger,
        enforce_verified_domains=False,
        delivery_max_attempts=1,
    )
    return TestClient(app)


def test_send_response_exposes_real_failure_reason() -> None:
    repo = LedgerRepository(":memory:")
    provider = FailingProvider("no MX records found for domain 'example.com'")
    client = _client(repo, provider)
    campaign_id = client.post("/campaigns", json={"name": "Fail"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
            "non_smtp_delivery": True,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["sent"] == 0
    assert body["failed"] == 1
    assert body["failures"][0]["recipient"] == "a@example.com"
    assert "no MX records found" in body["failures"][0]["error"]
    assert repo.recipient_status_rollups(campaign_id)[Status.FAILED] == 1


def test_messages_endpoint_reports_dead_letter_with_reason() -> None:
    repo = LedgerRepository(":memory:")
    provider = FailingProvider("relay refused: 550 blocked")
    client = _client(repo, provider)
    campaign_id = client.post("/campaigns", json={"name": "Fail"}).json()["id"]
    client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
        },
    )

    messages = client.get(f"/campaigns/{campaign_id}/messages")
    payload = messages.json()
    assert messages.status_code == 200
    assert payload["failed_count"] == 1
    assert payload["dead_letter"][0]["status"] == Status.FAILED.value
    assert "550 blocked" in payload["dead_letter"][0]["error"]


def test_retry_with_backoff_recovers_and_does_not_fake_success() -> None:
    repo = LedgerRepository(":memory:")
    provider = FlakyProvider(fail_times=2)
    logger = LoggingService(LogRepository(":memory:"))
    slept: list[float] = []
    service = DeliveryService(
        repo,
        provider,
        logger=logger,
        max_attempts=3,
        backoff_base=0.5,
        backoff_factor=2.0,
        sleeper=slept.append,
    )
    campaign = repo.create_campaign("Retry")

    receipts = service.send_campaign(
        campaign,
        ["a@example.com"],
        "Hi",
        "Body",
        sender="sender@example.com",
        delivery_channel="non_smtp",
    )

    assert provider.calls == 3
    assert receipts[0].result.success is True
    assert receipts[0].attempts == 3
    # Exponential backoff between the three attempts: 0.5s then 1.0s.
    assert slept == [0.5, 1.0]
    assert repo.recipient_status_rollups(campaign.id)[Status.SENT] == 1


def test_retry_exhausted_keeps_failure_reason() -> None:
    repo = LedgerRepository(":memory:")
    provider = FailingProvider("connection timed out")
    service = DeliveryService(repo, provider, max_attempts=3, backoff_base=0.0)
    campaign = repo.create_campaign("Retry")

    receipts = service.send_campaign(
        campaign, ["a@example.com"], "Hi", "Body", sender="sender@example.com"
    )

    assert provider.calls == 3
    assert receipts[0].result.success is False
    assert receipts[0].attempts == 3
    assert receipts[0].result.error == "connection timed out"
    assert repo.latest_error_for_message(receipts[0].message.id) == "connection timed out"
