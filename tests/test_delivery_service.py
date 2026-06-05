from backend.models import Campaign, Status
from backend.repositories import LedgerRepository
from backend.services import DeliveryProvider, DeliveryResult, DeliveryService
from backend.services import delivery as delivery_module


class FakeProvider(DeliveryProvider):
    def __init__(self, *, success=True, raise_error=False):
        self.success = success
        self.raise_error = raise_error
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        if self.raise_error:
            raise RuntimeError("boom")
        if self.success:
            return DeliveryResult(True, provider_message_id="provider-1")
        return DeliveryResult(False, error="rejected")


def test_full_queued_to_sent_path_with_fake_provider():
    repo = LedgerRepository(":memory:")
    provider = FakeProvider()
    service = DeliveryService(repo, provider)

    receipts = service.send_campaign(Campaign("Campaign"), ["a@example.com"], "Hi", "Body", sender="s@example.com")

    assert receipts[0].result.success is True
    assert len(provider.messages) == 1
    message = receipts[0].message
    assert message.status == Status.SENT
    assert [event.status for event in repo.list_events_for_message(message.id)] == [
        Status.QUEUED,
        Status.PROCESSING,
        Status.SENT,
    ]
    assert repo.recipient_status_rollups(message.campaign_id)[Status.SENT] == 1


def test_failed_path_on_provider_result_and_exception():
    repo = LedgerRepository(":memory:")
    service = DeliveryService(repo, FakeProvider(success=False))
    receipt = service.send_campaign(Campaign("Failure"), ["a@example.com"], "Hi", "Body", sender="s@example.com")[0]
    assert receipt.message.status == Status.FAILED
    assert repo.list_events_for_message(receipt.message.id)[-1].error == "rejected"

    repo2 = LedgerRepository(":memory:")
    service2 = DeliveryService(repo2, FakeProvider(raise_error=True))
    receipt2 = service2.send_campaign(Campaign("Exception"), ["b@example.com"], "Hi", "Body", sender="s@example.com")[0]
    assert receipt2.message.status == Status.FAILED
    assert "boom" in repo2.list_events_for_message(receipt2.message.id)[-1].error


def test_delivery_uses_single_mime_helper(monkeypatch):
    calls = []

    def fake_builder(sender, recipient, subject, content, *, html=False, attachments=None):
        calls.append((sender, recipient, subject, content, html))

        class Message(dict):
            def get(self, key, default=None):
                return "mime-id" if key == "Message-ID" else default

        return Message()

    class FakeSMTP:
        def __init__(self):
            self.sent = []

        def send_message(self, msg):
            self.sent.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(delivery_module, "build_mime_message", fake_builder)
    provider = delivery_module.SMTPDeliveryProvider(
        delivery_module.SMTPConfig("smtp.example.com", 587),
        smtp_factory=lambda config, context: FakeSMTP(),
    )

    result = provider.send(delivery_module.OutboundMessage("s@example.com", "r@example.com", "Hi", "Body", html=True))

    assert result.success is True
    assert calls == [("s@example.com", "r@example.com", "Hi", "Body", True)]
