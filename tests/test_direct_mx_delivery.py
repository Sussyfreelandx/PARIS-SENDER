"""Tests for the direct-to-MX (non-SMTP) delivery provider and channel selection."""

from __future__ import annotations

import ssl

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import Status
from backend.repositories import DomainRepository, LedgerRepository, LogRepository
from backend.services import (
    DeliverabilityService,
    DirectMxConfig,
    DirectMxDeliveryProvider,
    DomainService,
    LoggingService,
    OutboundMessage,
)
from tests.conftest import FakeResolver


class FakeSMTP:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.quit_called = False

    def send_message(self, message: object) -> None:
        self.sent.append(message)

    def quit(self) -> None:
        self.quit_called = True


class FakeMxResolver:
    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def resolve_mx(self, domain: str) -> list[str]:
        return self.mapping.get(domain, [])


def test_direct_mx_delivers_to_first_mx_host() -> None:
    used: list[str] = []
    fake = FakeSMTP()

    def factory(host: str, config: DirectMxConfig, context: ssl.SSLContext):
        used.append(host)
        return fake

    provider = DirectMxDeliveryProvider(
        mx_resolver=FakeMxResolver({"example.com": ["mx1.example.com", "mx2.example.com"]}),
        smtp_factory=factory,
    )
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))

    assert result.success is True
    assert used == ["mx1.example.com"]
    assert len(fake.sent) == 1
    assert fake.quit_called is True


def test_direct_mx_falls_back_to_next_host_on_failure() -> None:
    used: list[str] = []

    def factory(host: str, config: DirectMxConfig, context: ssl.SSLContext):
        used.append(host)
        if host == "mx1.example.com":
            raise OSError("connection refused")
        return FakeSMTP()

    provider = DirectMxDeliveryProvider(
        mx_resolver=FakeMxResolver({"example.com": ["mx1.example.com", "mx2.example.com"]}),
        smtp_factory=factory,
    )
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))

    assert result.success is True
    assert used == ["mx1.example.com", "mx2.example.com"]


def test_direct_mx_reports_missing_mx_records() -> None:
    provider = DirectMxDeliveryProvider(mx_resolver=FakeMxResolver({}))
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))
    assert result.success is False
    assert "no MX records" in (result.error or "")
    assert "no_mx_records_found" in (result.error or "")
    assert result.classification == "PERM_FAIL"
    assert result.stage == "mx"


def test_direct_mx_reports_invalid_recipient() -> None:
    provider = DirectMxDeliveryProvider(mx_resolver=FakeMxResolver({}))
    result = provider.send(OutboundMessage("s@sender.com", "not-an-email", "Hi", "Body"))
    assert result.success is False
    assert "invalid recipient" in (result.error or "")
    assert result.classification == "PERM_FAIL"
    assert result.stage == "recipient"


def test_direct_mx_blocked_port_is_classified_and_preserved() -> None:
    # Every MX host refuses the connection on port 25 (the classic ISP block).
    def factory(host: str, config: DirectMxConfig, context: ssl.SSLContext):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    provider = DirectMxDeliveryProvider(
        mx_resolver=FakeMxResolver({"example.com": ["mx1.example.com", "mx2.example.com"]}),
        smtp_factory=factory,
    )
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))

    assert result.success is False
    assert result.classification == "BLOCKED"
    assert result.stage == "connect"
    assert "connection_blocked_or_rejected" in (result.error or "")
    # Real per-host reasons are preserved across the whole MX fallback chain:
    # both attempted hosts are reported (one "MX host" entry each).
    assert (result.error or "").count("MX host '") == 2


def test_direct_mx_permanent_smtp_rejection_is_perm_fail() -> None:
    import smtplib

    class RejectingSMTP(FakeSMTP):
        def send_message(self, message: object) -> None:
            raise smtplib.SMTPResponseException(550, "5.7.1 message rejected")

    provider = DirectMxDeliveryProvider(
        mx_resolver=FakeMxResolver({"example.com": ["mx1.example.com"]}),
        smtp_factory=lambda host, config, context: RejectingSMTP(),
    )
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))

    assert result.success is False
    assert result.classification == "PERM_FAIL"
    assert "550" in (result.error or "")


def test_direct_mx_temporary_smtp_error_is_temp_fail() -> None:
    import smtplib

    class GreylistingSMTP(FakeSMTP):
        def send_message(self, message: object) -> None:
            raise smtplib.SMTPResponseException(451, "4.7.1 greylisted, try again later")

    provider = DirectMxDeliveryProvider(
        mx_resolver=FakeMxResolver({"example.com": ["mx1.example.com"]}),
        smtp_factory=lambda host, config, context: GreylistingSMTP(),
    )
    result = provider.send(OutboundMessage("s@sender.com", "user@example.com", "Hi", "Body"))

    assert result.success is False
    assert result.classification == "TEMP_FAIL"
    assert "451" in (result.error or "")


def _client(repo: LedgerRepository) -> TestClient:
    domains = DomainService(DomainRepository(":memory:"), resolver=FakeResolver())
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, threshold=0, logger=logger),
        logging_service=logger,
        enforce_verified_domains=False,
    )
    return TestClient(app)


def test_smtp_config_in_request_builds_provider_and_sends(monkeypatch) -> None:
    # When the request carries SMTP config, a provider is built per-send. We patch
    # the SMTP factory layer by injecting a fake transport via monkeypatching smtplib.
    import backend.services.delivery as delivery_module

    captured = {}

    class FakeClient:
        def send_message(self, message):
            captured["message"] = message

        def quit(self):
            captured["quit"] = True

    def fake_smtp(host, port, timeout=0):
        captured["host"] = host
        captured["port"] = port

        class Conn(FakeClient):
            def starttls(self, context=None):
                captured["starttls"] = True

            def login(self, user, password):
                captured["login"] = (user, password)

        return Conn()

    monkeypatch.setattr(delivery_module.smtplib, "SMTP", fake_smtp)

    repo = LedgerRepository(":memory:")
    client = _client(repo)
    campaign_id = client.post("/campaigns", json={"name": "SMTP"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
            "smtp": {"host": "smtp.example.com", "port": 587, "username": "u", "password": "p"},
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery_channel"] == "smtp"
    assert captured["host"] == "smtp.example.com"
    assert captured["port"] == 587
    assert captured["login"] == ("u", "p")
    assert repo.recipient_status_rollups(campaign_id)[Status.SENT] == 1


def test_non_smtp_config_in_request_uses_direct_mx(monkeypatch) -> None:
    import backend.services.delivery as delivery_module

    captured = {}

    def fake_smtp(host, port, timeout=0):
        captured["host"] = host

        class Conn:
            def starttls(self, context=None):
                pass

            def send_message(self, message):
                captured["sent"] = True

            def quit(self):
                pass

        return Conn()

    monkeypatch.setattr(delivery_module.smtplib, "SMTP", fake_smtp)

    def fake_resolve_mx(self, domain):
        return ["mx1.example.com"]

    monkeypatch.setattr(delivery_module.DnspythonMxResolver, "resolve_mx", fake_resolve_mx)

    repo = LedgerRepository(":memory:")
    client = _client(repo)
    campaign_id = client.post("/campaigns", json={"name": "MX"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={
            "recipients": ["a@example.com"],
            "subject": "Hi",
            "content": "Body",
            "sender": "sender@example.com",
            "non_smtp_delivery": True,
            "non_smtp": {"port": 25},
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery_channel"] == "non_smtp"
    assert captured["host"] == "mx1.example.com"
    assert captured.get("sent") is True
