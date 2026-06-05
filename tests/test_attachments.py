"""Tests for file attachment support across MIME, providers, and the send API."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import Status
from backend.repositories import DomainRepository, LedgerRepository, LogRepository
from backend.services import (
    Attachment,
    DeliverabilityService,
    DeliveryProvider,
    DeliveryResult,
    DomainService,
    LoggingService,
    OutboundMessage,
    build_mime_message,
)
from tests.conftest import FakeResolver


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_attachment_from_base64_decodes_content() -> None:
    attachment = Attachment.from_base64("report.pdf", _b64(b"%PDF-1.4 data"), "application/pdf")
    assert attachment.filename == "report.pdf"
    assert attachment.content == b"%PDF-1.4 data"
    assert attachment.mime_type == "application/pdf"


def test_attachment_from_base64_rejects_invalid_payload() -> None:
    try:
        Attachment.from_base64("bad.bin", "!!!not base64!!!")
    except ValueError as exc:
        assert "invalid base64" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError for invalid base64")


def test_build_mime_message_without_attachments_is_alternative() -> None:
    message = build_mime_message("s@example.com", "r@example.com", "Hi", "Body")
    assert message.get_content_type() == "multipart/alternative"


def test_build_mime_message_with_attachment_is_mixed_with_part() -> None:
    attachment = Attachment.from_base64("note.txt", _b64(b"hello"), "text/plain")
    message = build_mime_message("s@example.com", "r@example.com", "Hi", "Body", attachments=[attachment])
    assert message.get_content_type() == "multipart/mixed"
    dispositions = [part.get_content_disposition() for part in message.walk()]
    assert "attachment" in dispositions
    attached = [part for part in message.walk() if part.get_content_disposition() == "attachment"][0]
    assert attached.get_filename() == "note.txt"
    assert attached.get_payload(decode=True) == b"hello"


class RecordingProvider(DeliveryProvider):
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> DeliveryResult:
        self.messages.append(message)
        return DeliveryResult(True, provider_message_id=f"rec-{len(self.messages)}")


def _client(repo: LedgerRepository, provider: DeliveryProvider) -> TestClient:
    resolver = FakeResolver()
    domains = DomainService(DomainRepository(":memory:"), resolver=resolver)
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        provider=provider,
        non_smtp_provider=provider,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, threshold=0, logger=logger),
        logging_service=logger,
        enforce_verified_domains=False,
    )
    return TestClient(app)


def test_send_forwards_attachments_to_provider() -> None:
    repo = LedgerRepository(":memory:")
    provider = RecordingProvider()
    client = _client(repo, provider)
    campaign_id = client.post("/campaigns", json={"name": "Attach"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
            "attachments": [
                {"filename": "doc.txt", "content_base64": _b64(b"file-bytes"), "mime_type": "text/plain"}
            ],
        },
    )

    assert response.status_code == 200
    assert len(provider.messages) == 1
    attachments = provider.messages[0].attachments
    assert attachments is not None and len(attachments) == 1
    assert attachments[0].filename == "doc.txt"
    assert attachments[0].content == b"file-bytes"
    assert repo.recipient_status_rollups(campaign_id)[Status.SENT] == 1


def test_send_rejects_invalid_attachment_base64() -> None:
    repo = LedgerRepository(":memory:")
    provider = RecordingProvider()
    client = _client(repo, provider)
    campaign_id = client.post("/campaigns", json={"name": "Attach"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
            "attachments": [{"filename": "doc.txt", "content_base64": "###", "mime_type": "text/plain"}],
        },
    )

    assert response.status_code == 400
    assert "invalid base64" in response.json()["detail"]
    assert provider.messages == []
