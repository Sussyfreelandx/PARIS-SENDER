"""Unit tests for warmup ramping, limits, events, and overrides."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models import WarmupConfig, WarmupEventType
from backend.repositories import WarmupRepository
from backend.services import WarmupService


class FixedClock:
    """Mutable UTC clock for deterministic warmup tests."""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        """Advance the clock by a timedelta."""
        self.current += timedelta(**kwargs)


def _service(clock: FixedClock | None = None) -> WarmupService:
    return WarmupService(WarmupRepository(":memory:"), clock=clock)


def test_ramp_up_daily_limit_increases_over_days() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=100, max_per_batch=100, max_per_hour=100, ramp_start_limit=10, ramp_days=4))

    assert service.current_daily_limit("example.com") == 10
    clock.advance(days=1)
    assert service.current_daily_limit("example.com") == 40
    clock.advance(days=1)
    assert service.current_daily_limit("example.com") == 70
    clock.advance(days=1)
    assert service.current_daily_limit("example.com") == 100


def test_check_send_allows_within_limits_and_blocks_batch_daily_hourly() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=5, max_per_batch=3, max_per_hour=4, ramp_start_limit=5, ramp_days=1))

    allowed = service.check_send("example.com", 3)
    assert allowed.blocked is False
    assert allowed.allowed_count == 3

    batch_block = service.check_send("example.com", 4)
    assert batch_block.blocked is True
    assert "max_per_batch" in batch_block.reason

    service.record_execution("example.com", 1, 4)
    hour_block = service.check_send("example.com", 1)
    assert hour_block.blocked is True
    assert "hourly" in hour_block.reason

    clock.advance(hours=1, seconds=1)
    daily_block = service.check_send("example.com", 2)
    assert daily_block.blocked is True
    assert daily_block.allowed_count == 1
    assert "capacity" in daily_block.reason


def test_rolling_windows_expire_with_injected_clock() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=10, max_per_batch=10, max_per_hour=3, ramp_start_limit=10, ramp_days=1))
    service.record_execution("example.com", 1, 3)

    assert service.check_send("example.com", 1).blocked is True
    clock.advance(hours=1, seconds=1)
    assert service.check_send("example.com", 3).blocked is False
    assert service.progress("example.com").sent_today == 3

    clock.advance(hours=23, seconds=1)
    progress = service.progress("example.com")
    assert progress.sent_today == 0
    assert progress.remaining_capacity == 3


def test_schedule_and_execution_events_are_recorded() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=10, max_per_batch=5, max_per_hour=5, ramp_start_limit=10, ramp_days=1))

    scheduled = service.schedule("example.com", 42, 4)
    executed = service.record_execution("example.com", 42, 4)
    events = service.events("example.com")

    assert scheduled["event"]["event_type"] == WarmupEventType.SCHEDULED.value
    assert executed["event_type"] == WarmupEventType.EXECUTED.value
    assert [event["event_type"] for event in events] == [WarmupEventType.EXECUTED.value, WarmupEventType.SCHEDULED.value]


def test_admin_override_raises_limits_and_records_event() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=2, max_per_batch=2, max_per_hour=2, ramp_start_limit=2, ramp_days=1))

    try:
        service.admin_override("example.com", authorized=False, daily_limit=10)
    except PermissionError:
        pass
    else:
        raise AssertionError("unauthorized override should fail")

    config = service.admin_override("example.com", authorized=True, daily_limit=10, max_per_batch=5, max_per_hour=5)
    assert config.daily_limit == 10
    assert service.check_send("example.com", 5).blocked is False
    assert service.events("example.com")[0]["event_type"] == WarmupEventType.OVERRIDE.value


def test_progress_next_batch_values_when_throttled() -> None:
    clock = FixedClock(datetime(2025, 1, 1, 9, tzinfo=timezone.utc))
    service = _service(clock)
    service.enable_domain("example.com", WarmupConfig(daily_limit=3, max_per_batch=3, max_per_hour=3, ramp_start_limit=3, ramp_days=1))
    service.record_execution("example.com", 1, 3)

    progress = service.progress("example.com")
    assert progress.throttled is True
    assert progress.remaining_capacity == 0
    assert progress.next_batch_at == clock.current + timedelta(hours=24)
