"""Unit tests for the Phase 8 health monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.models import ComponentHealth, Domain, Event, HealthServer, HealthStatus, Message, Status
from backend.repositories import DomainRepository, LedgerRepository, WarmupRepository
from backend.services import DomainService, HealthMonitorService, WarmupService
from backend.models import WarmupConfig


class FixedClock:
    """Mutable UTC clock for deterministic health tests."""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        """Advance the clock by a timedelta."""
        self.current += timedelta(**kwargs)


def _probe(statuses: dict[str, HealthStatus]):
    def check(server: HealthServer, *, now: datetime) -> ComponentHealth:
        return ComponentHealth(
            server.id,
            server.kind,
            statuses.get(server.id, HealthStatus.OK),
            f"{server.id} checked",
            {"host": server.host, "tls": server.kind == "smtp", "ehlo": "250 ok"},
            now,
        )

    return check


def _ledger_with_activity(clock: FixedClock) -> LedgerRepository:
    ledger = LedgerRepository(":memory:")
    campaign = ledger.create_campaign("Health")
    queued_recipient = ledger.add_recipient(campaign.id, "queued@example.com")
    ledger.create_message(Message(campaign.id, queued_recipient.id, "Queued", "Body"))
    processing_recipient = ledger.add_recipient(campaign.id, "processing@example.com")
    processing = ledger.create_message(Message(campaign.id, processing_recipient.id, "Processing", "Body"))
    ledger.record_event(Event(message_id=processing.id, status=Status.PROCESSING, created_at=clock.current))
    sent_recipient = ledger.add_recipient(campaign.id, "sent@example.com")
    sent = ledger.create_message(Message(campaign.id, sent_recipient.id, "Sent", "Body"))
    ledger.record_event(Event(message_id=sent.id, status=Status.SENT, created_at=clock.current))
    return ledger


def test_snapshot_degrades_for_failed_servers_unverified_domains_and_queue_metrics() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 12, tzinfo=timezone.utc))
    ledger = _ledger_with_activity(clock)
    domains = DomainService(DomainRepository(":memory:"))
    domains.repository.create(Domain(name="bad.example", dkim_verified=False, spf_verified=False, dmarc_verified=False))
    warmup = WarmupService(WarmupRepository(":memory:"), clock=clock)
    warmup.enable_domain("bad.example", WarmupConfig(daily_limit=1, max_per_batch=1, max_per_hour=1, ramp_start_limit=1, ramp_days=1))
    warmup.record_execution("bad.example", 1, 1)
    service = HealthMonitorService(
        ledger=ledger,
        domain_service=domains,
        warmup_service=warmup,
        servers=[
            {"id": "smtp-1", "host": "smtp.local", "kind": "smtp"},
            {"id": "mx-1", "host": "mx.local", "kind": "mx"},
            {"id": "proxy-1", "host": "proxy.local", "kind": "proxy"},
        ],
        smtp_probe=_probe({"smtp-1": HealthStatus.CRITICAL}),
        mx_probe=_probe({"mx-1": HealthStatus.WARN}),
        server_probe=_probe({"proxy-1": HealthStatus.CRITICAL}),
        clock=clock,
    )

    snapshot = service.snapshot()

    assert snapshot["overall_status"] == HealthStatus.CRITICAL.value
    assert snapshot["queue_depth"]["queued_messages"] == 1
    assert snapshot["queue_depth"]["processing_messages"] == 1
    assert snapshot["queue_depth"]["queued_recipients"] == 1
    assert snapshot["queue_depth"]["processing_recipients"] == 1
    assert snapshot["throughput"]["sent"] == 1
    assert snapshot["domain_alerts"][0]["domain"] == "bad.example"
    assert snapshot["domain_alerts"][0]["status"] == HealthStatus.CRITICAL.value
    assert any(server["server_id"] == "proxy-1" and server["status"] == "red" for server in snapshot["servers"])
    warmup_component = next(component for component in snapshot["components"] if component["kind"] == "warmup")
    assert warmup_component["status"] == HealthStatus.WARN.value
    assert warmup_component["metrics"]["throttled"][0]["domain"] == "bad.example"


def test_domain_and_server_detail_methods_are_deterministic() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 12, tzinfo=timezone.utc))
    domains = DomainService(DomainRepository(":memory:"))
    domains.repository.create(Domain(name="good.example", dkim_verified=True, spf_verified=True, dmarc_verified=True))
    service = HealthMonitorService(
        domain_service=domains,
        servers=[{"id": "smtp-1", "host": "smtp.local", "kind": "smtp"}],
        smtp_probe=_probe({"smtp-1": HealthStatus.OK}),
        clock=clock,
    )

    domain = service.domain_health("GOOD.example")
    server = service.server_health("smtp-1")

    assert domain["status"] == HealthStatus.OK.value
    assert domain["health_score"] == 100
    assert len(domain["records"]) == 3
    assert server["status"] == HealthStatus.OK.value
    assert server["metrics"]["tls"] is True


def test_run_monitor_refreshes_cached_snapshot_until_stopped() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 12, tzinfo=timezone.utc))
    service = HealthMonitorService(clock=clock)

    async def run_once() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(service.run_monitor(interval_seconds=0.01, stop=stop))
        await asyncio.sleep(0)
        stop.set()
        await task

    asyncio.run(run_once())

    assert service.latest_snapshot is not None
    assert service.latest_snapshot["generated_at"] == clock.current.isoformat()
