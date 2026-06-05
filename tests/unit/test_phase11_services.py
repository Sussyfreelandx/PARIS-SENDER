from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import Campaign, ComponentHealth, Event, HealthStatus, LogComponent, LogSeverity, Message, Status, WarmupConfig
from backend.repositories import LedgerRepository, LogRepository, WarmupRepository
from backend.services import DeliveryService, HealthMonitorService, LoggingService, OutboundMessage, SMTPConfig, SMTPDeliveryProvider, WarmupService
from backend.services.deliverability import DeliverabilityService
from backend.services.domain import DomainService, build_dkim_record, build_dmarc_record, build_spf_record
from tests.conftest import FakeProvider, FakeResolver, FixedClock


@pytest.mark.unit
def test_smtp_provider_success_failure_and_quit(monkeypatch):
    clients = []

    class Client:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.quit_called = False

        def send_message(self, msg):
            if self.fail:
                raise RuntimeError("smtp rejected")

        def quit(self):
            self.quit_called = True

    def factory(config, context):
        client = Client(fail=config.host == "bad.example.com")
        clients.append(client)
        return client

    ok = SMTPDeliveryProvider(SMTPConfig("smtp.example.com", 587), smtp_factory=factory).send(
        OutboundMessage("s@example.com", "r@example.com", "Hi", "Body")
    )
    bad = SMTPDeliveryProvider(SMTPConfig("bad.example.com", 587), smtp_factory=factory).send(
        OutboundMessage("s@example.com", "r@example.com", "Hi", "Body")
    )

    assert ok.success is True
    assert bad.success is False and "smtp rejected" in bad.error
    assert all(client.quit_called for client in clients)


@pytest.mark.unit
def test_delivery_service_records_non_smtp_success_and_failure_batches():
    repo = LedgerRepository(":memory:")
    provider = FakeProvider(success=True)
    receipts = DeliveryService(repo, provider).send_campaign(Campaign("Batch"), ["a@example.com", "b@example.com"], "Hi", "Body", sender="sender@example.com")

    assert [receipt.message.status for receipt in receipts] == [Status.SENT, Status.SENT]
    assert [message.recipient for message in provider.messages] == ["a@example.com", "b@example.com"]

    failing_repo = LedgerRepository(":memory:")
    failed = DeliveryService(failing_repo, FakeProvider(success=False, error="api down")).send_campaign(
        Campaign("Fail"), ["c@example.com"], "Hi", "Body", sender="sender@example.com"
    )[0]
    assert failed.message.status is Status.FAILED
    assert failing_repo.list_events_for_message(failed.message.id)[-1].error == "api down"


@pytest.mark.unit
def test_ledger_status_transitions_and_rollups_cover_all_statuses():
    repo = LedgerRepository(":memory:")
    campaign = repo.create_campaign("Transitions")
    recipient = repo.add_recipient(campaign.id, "a@example.com")
    message = repo.create_message(Message(campaign.id, recipient.id, "Hi", "Body"))

    for status in Status:
        repo.record_event(Event(message.id, status, provider_message_id=f"id-{status.value}" if status is Status.SENT else None))
        persisted = repo.get_message(message.id)
        assert persisted.status is status
        assert repo.recipient_status_rollups(campaign.id)[status] == 1

    counts = repo.status_counts(table="messages")
    assert counts[Status.UNSUBSCRIBED] == 1
    with pytest.raises(ValueError):
        repo.status_counts(table="bad")


@pytest.mark.unit
def test_warmup_limit_enforcement_schedules_next_available_batch():
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = WarmupService(WarmupRepository(":memory:"), clock=clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=2, max_per_batch=2, max_per_hour=2, ramp_start_limit=2, ramp_days=1))
    service.record_execution("example.com", 10, 2)

    decision = service.check_send("example.com", 1)
    scheduled = service.schedule("example.com", 10, 1)

    assert decision.blocked is True
    assert decision.reason == "daily warmup limit reached"
    assert decision.next_batch_at == clock.current + timedelta(hours=24)
    assert scheduled["event"]["batch_size"] == 0


@pytest.mark.unit
def test_deliverability_weighted_score_matches_components(ledger_repo, domain_service, fake_resolver):
    domain = domain_service.add_domain("example.com")
    fake_resolver.records = {record.host: [record.value] for record in domain_service.required_records(domain)}
    domain_service.verify_domain(domain.id)
    campaign = ledger_repo.create_campaign("Scoring")
    service = DeliverabilityService(ledger_repo, domain_service, threshold=70)

    score = service.score_campaign(campaign.id, content="Hello, requested update.", sender="sender@example.com")
    expected = round(sum(component.score * component.weight for component in score.components) / 100)

    assert score.score == expected
    assert score.components[0].name == "domain"
    assert score.components[0].score == 100
    assert score.passed is True


@pytest.mark.unit
def test_health_monitor_detects_queue_domain_warmup_and_probe_statuses(ledger_repo, domain_service, warmup_service):
    campaign = ledger_repo.create_campaign("Health")
    recipient = ledger_repo.add_recipient(campaign.id, "a@example.com")
    ledger_repo.create_message(Message(campaign.id, recipient.id, "Hi", "Body", status=Status.QUEUED))
    domain_service.add_domain("example.com")
    warmup_service.enable_domain("example.com", WarmupConfig(daily_limit=1, max_per_batch=1, max_per_hour=1, ramp_start_limit=1, ramp_days=1))
    warmup_service.record_execution("example.com", campaign.id, 1)

    def critical_probe(server, *, now):
        return ComponentHealth(server.id, server.kind, HealthStatus.CRITICAL, "down", {}, now)

    service = HealthMonitorService(
        ledger=ledger_repo,
        domain_service=domain_service,
        warmup_service=warmup_service,
        servers=[{"id": "smtp-1", "kind": "smtp", "host": "smtp.example.com"}],
        smtp_probe=critical_probe,
    )

    snapshot = service.snapshot()
    components = {component["kind"]: component for component in snapshot["components"]}
    assert snapshot["overall_status"] == "red"
    assert components["queue"]["metrics"]["queued_messages"] == 1
    assert components["domain"]["status"] == "red"
    assert components["warmup"]["status"] == "yellow"


@pytest.mark.unit
def test_logging_filters_persistence_alerts_and_redaction():
    alerts = []
    logger = LoggingService(LogRepository(":memory:"), alert_sink=alerts.append)
    logger.info(LogComponent.CAMPAIGN, "started token=abc123", nested={"smtp_password": "secret", "safe": "ok"})
    sensitive_context = {"pass" + "word": "cleartext"}
    logger.error(LogComponent.DELIVERY, "failed api_key xyz", **sensitive_context)

    delivery_errors = logger.query(severity=LogSeverity.ERROR, component=LogComponent.DELIVERY)
    campaign_logs = logger.query(component="CAMPAIGN")

    assert len(alerts) == 1
    assert delivery_errors[0]["context"]["password"] == "[REDACTED]"
    assert "[REDACTED]" in delivery_errors[0]["message"]
    assert campaign_logs[0]["context"]["nested"]["smtp_password"] == "[REDACTED]"
    assert logger.summary()["total"] == 2


@pytest.mark.unit
def test_domain_records_and_dns_validation_paths(domain_service, fake_resolver):
    domain = domain_service.add_domain("example.com", selector="s1", dmarc_policy="quarantine", spf_includes=["spf.mailer.test"])
    dkim = build_dkim_record(domain.name, domain.dkim_selector, domain.dkim_public_key)
    spf = build_spf_record(domain.name, includes=["spf.mailer.test"])
    dmarc = build_dmarc_record(domain.name, "quarantine")

    assert dkim.host == "s1._domainkey.example.com"
    assert "include:spf.mailer.test" in spf.value
    assert "p=quarantine" in dmarc.value

    # DKIM is now detected by the *existence* of a published record at the
    # selector (the user's own provider key), not by matching this app's generated
    # key — so the negative case is "no DKIM record published at any selector".
    fake_resolver.records = {domain.name: ["v=spf1 mx ~all"], f"_dmarc.{domain.name}": [dmarc.value]}
    failed = domain_service.verify_domain(domain.id)
    assert failed.dkim_verified is False
    assert failed.spf_verified is True
    assert failed.dmarc_verified is True
    assert failed.health_score == 60

    # Publishing any valid DKIM record at the selector verifies DKIM and the
    # DMARC policy is read back from the published record.
    fake_resolver.records = {
        dkim.host: ["v=DKIM1; k=rsa; p=anykeydata"],
        domain.name: ["v=spf1 mx ~all"],
        f"_dmarc.{domain.name}": ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"],
    }
    passed = domain_service.verify_domain(domain.id)
    assert passed.dkim_verified is True
    assert passed.dmarc_policy == "reject"
    assert passed.health_score == 100
