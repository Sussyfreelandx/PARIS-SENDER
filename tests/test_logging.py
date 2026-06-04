"""Unit tests for centralized logging repository and service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models import LogComponent, LogSeverity
from backend.repositories import LogRepository
from backend.services import LoggingService


class FixedClock:
    """Mutable UTC clock for deterministic logging tests."""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        """Advance the clock by a timedelta."""
        self.current += timedelta(**kwargs)


def _service(clock: FixedClock | None = None, alert_sink=None) -> LoggingService:
    return LoggingService(LogRepository(":memory:"), clock=clock, alert_sink=alert_sink)


def test_log_entry_context_round_trips_and_filters() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.info(LogComponent.CAMPAIGN, "campaign started", campaign_id=1, nested={"path": "smtp"})
    clock.advance(minutes=5)
    service.error(LogComponent.DELIVERY, "delivery failed", campaign_id=1, error="boom")

    delivery = service.query(severity=LogSeverity.ERROR, component=LogComponent.DELIVERY)
    assert len(delivery) == 1
    assert delivery[0]["context"] == {"campaign_id": 1, "error": "boom"}

    campaign = service.query(component="CAMPAIGN")
    assert campaign[0]["context"]["nested"] == {"path": "smtp"}


def test_query_filters_by_timestamp_range_newest_first() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.debug(LogComponent.API, "early")
    clock.advance(hours=1)
    since = clock.current
    service.info(LogComponent.API, "middle")
    clock.advance(hours=1)
    until = clock.current
    service.warning(LogComponent.API, "late")
    clock.advance(hours=1)
    service.critical(LogComponent.API, "too late")

    logs = service.query(since=since, until=until, limit=10)

    assert [entry["message"] for entry in logs] == ["late", "middle"]


def test_summary_counts_by_severity_and_component() -> None:
    service = _service(FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc)))
    service.info(LogComponent.CAMPAIGN, "one")
    service.warning(LogComponent.CAMPAIGN, "two")
    service.error(LogComponent.HEALTH, "three")

    summary = service.summary()

    assert summary["total"] == 3
    assert summary["by_severity"]["INFO"] == 1
    assert summary["by_severity"]["WARNING"] == 1
    assert summary["by_component"]["CAMPAIGN"] == 2
    assert summary["earliest_timestamp"] == "2025-01-01T09:00:00+00:00"


def test_archive_deletes_logs_older_than_cutoff() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.info(LogComponent.API, "old")
    clock.advance(days=2)
    service.info(LogComponent.API, "new")

    archived = service.archive(clock.current - timedelta(days=1))

    assert archived == 1
    assert service.repository.count() == 1
    assert service.query()[0]["message"] == "new"


def test_critical_alert_hook_fires() -> None:
    alerts = []
    service = _service(FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc)), alerts.append)

    service.critical(LogComponent.HEALTH, "red alert", component_id="server-1")

    assert len(alerts) == 1
    assert alerts[0].severity is LogSeverity.CRITICAL
    assert alerts[0].context == {"component_id": "server-1"}
