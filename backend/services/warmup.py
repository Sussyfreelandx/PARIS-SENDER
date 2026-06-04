"""Warmup service enforcing per-domain ramp-up limits."""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.models import LogComponent, WarmupConfig, WarmupEventType, WarmupStatus
from backend.repositories import WarmupRepository

Clock = Callable[[], datetime]


@dataclass(slots=True)
class WarmupDecision:
    """Structured warmup allowance decision for one prospective send."""

    allowed_count: int
    requested_count: int
    blocked: bool
    reason: str
    next_batch_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision for API callers."""
        return {
            "allowed_count": self.allowed_count,
            "requested_count": self.requested_count,
            "blocked": self.blocked,
            "reason": self.reason,
            "next_batch_at": self.next_batch_at.isoformat() if self.next_batch_at else None,
        }


class WarmupService:
    """Coordinates warmup configuration, ramp calculations, and send gates."""

    def __init__(self, repository: WarmupRepository, clock: Clock | None = None, logger: Any | None = None) -> None:
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.logger = logger

    def enable_domain(self, domain: str, config: WarmupConfig | None = None) -> WarmupConfig:
        """Enable warmup for a domain with the supplied or default config."""
        stored = self.repository.upsert_config(domain, config or WarmupConfig(), now=self._now())
        self._log("INFO", "warmup domain configured", domain=domain.strip().lower(), config=stored.to_dict())
        return stored

    def is_warmup(self, domain: str | None) -> bool:
        """Return whether a domain is currently warmup-enabled."""
        if not domain:
            return False
        config = self.repository.get_config(domain)
        return bool(config and config.enabled)

    def list_domains(self) -> list[dict[str, Any]]:
        """List warmup-enabled domain configurations."""
        return [{"domain": domain, "config": config.to_dict()} for domain, config in self.repository.list_configs() if config.enabled]

    def current_daily_limit(self, domain: str) -> int:
        """Compute today's ramped daily send limit for a domain."""
        config = self._require_config(domain)
        return self._daily_limit_for(config, self._now())

    def check_send(self, domain: str, requested_count: int) -> WarmupDecision:
        """Return whether the requested batch fits current daily/hourly/batch warmup capacity."""
        config = self._require_config(domain)
        now = self._now()
        requested = max(0, int(requested_count))
        sent_today = self.repository.count_executed_since(domain, now - timedelta(hours=24))
        sent_hour = self.repository.count_executed_since(domain, now - timedelta(hours=1))
        daily_remaining = max(0, self._daily_limit_for(config, now) - sent_today)
        hourly_remaining = max(0, config.max_per_hour - sent_hour)
        allowed = max(0, min(daily_remaining, hourly_remaining, config.max_per_batch))
        if requested <= allowed:
            return WarmupDecision(allowed, requested, False, "allowed", None)
        reason = self._block_reason(requested, allowed, daily_remaining, hourly_remaining, config.max_per_batch)
        return WarmupDecision(allowed, requested, True, reason, self._next_batch_at(domain, daily_remaining, hourly_remaining))

    def schedule(self, domain: str, campaign_id: int | None, requested_count: int) -> dict[str, Any]:
        """Record a warmup scheduling event and return planned batch information."""
        decision = self.check_send(domain, requested_count)
        event = self.repository.append_event(
            domain,
            WarmupEventType.SCHEDULED,
            campaign_id=campaign_id,
            batch_size=min(requested_count, decision.allowed_count),
            detail=decision.reason,
            metadata=decision.to_dict(),
            created_at=self._now(),
        )
        self._log(
            "WARNING" if decision.blocked else "INFO",
            "warmup send scheduled",
            domain=domain.strip().lower(),
            campaign_id=campaign_id,
            decision=decision.to_dict(),
        )
        return {"decision": decision.to_dict(), "event": event}

    def record_execution(self, domain: str, campaign_id: int | None, batch_size: int) -> dict[str, Any]:
        """Record an executed warmup batch so rolling windows advance."""
        event = self.repository.append_event(
            domain,
            WarmupEventType.EXECUTED,
            campaign_id=campaign_id,
            batch_size=max(0, int(batch_size)),
            detail="batch executed",
            created_at=self._now(),
        )
        self._log("INFO", "warmup batch executed", domain=domain.strip().lower(), campaign_id=campaign_id, batch_size=max(0, int(batch_size)))
        return event

    def progress(self, domain: str) -> WarmupStatus:
        """Return current warmup progress for the UI."""
        config = self._require_config(domain)
        now = self._now()
        daily_limit = self._daily_limit_for(config, now)
        sent_today = self.repository.count_executed_since(domain, now - timedelta(hours=24))
        sent_hour = self.repository.count_executed_since(domain, now - timedelta(hours=1))
        remaining_today = max(0, daily_limit - sent_today)
        remaining_hour = max(0, config.max_per_hour - sent_hour)
        remaining_capacity = max(0, min(remaining_today, remaining_hour, config.max_per_batch))
        throttled = remaining_capacity <= 0
        return WarmupStatus(
            domain=domain.strip().lower(),
            current_day=self._current_day(config, now),
            daily_limit=daily_limit,
            sent_today=sent_today,
            sent_this_hour=sent_hour,
            remaining_today=remaining_today,
            remaining_this_hour=remaining_hour,
            remaining_capacity=remaining_capacity,
            max_per_batch=config.max_per_batch,
            next_batch_at=self._next_batch_at(domain, remaining_today, remaining_hour) if throttled else None,
            throttled=throttled,
        )

    def admin_override(
        self,
        domain: str,
        *,
        authorized: bool,
        daily_limit: int | None = None,
        max_per_hour: int | None = None,
        max_per_batch: int | None = None,
        bypass_remaining: bool = False,
        detail: str | None = None,
    ) -> WarmupConfig:
        """Apply an authorized local admin override to raise or bypass limits."""
        if not authorized:
            raise PermissionError("warmup override requires authorized=true")
        config = self._require_config(domain)
        if bypass_remaining:
            batch = max_per_batch_or_default(max_per_batch, config)
            current_day = self.repository.count_executed_since(domain, self._now() - timedelta(hours=24))
            current_hour = self.repository.count_executed_since(domain, self._now() - timedelta(hours=1))
            daily_limit = max(daily_limit or config.daily_limit, current_day + batch)
            max_per_hour = max(max_per_hour or config.max_per_hour, current_hour + batch)
            max_per_batch = max(max_per_batch or config.max_per_batch, batch)
        updated = WarmupConfig(
            daily_limit=max(config.daily_limit, daily_limit or config.daily_limit),
            max_per_batch=max(config.max_per_batch, max_per_batch or config.max_per_batch),
            max_per_hour=max(config.max_per_hour, max_per_hour or config.max_per_hour),
            ramp_start_limit=config.ramp_start_limit,
            ramp_days=config.ramp_days,
            enabled=config.enabled,
            start_date=config.start_date,
        )
        stored = self.repository.upsert_config(domain, updated, now=self._now())
        self.repository.append_event(
            domain,
            WarmupEventType.OVERRIDE,
            detail=detail or "authorized warmup override",
            metadata=stored.to_dict(),
            created_at=self._now(),
        )
        self._log("WARNING", "warmup override applied", domain=domain.strip().lower(), config=stored.to_dict(), detail=detail)
        return stored

    def events(self, domain: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent warmup events for a domain."""
        return self.repository.list_events(domain, limit=limit)

    async def run_ramp_scheduler(self, *, interval_seconds: float = 3600.0, stop: asyncio.Event | None = None) -> None:
        """Periodically touch ramp configs so long-running apps observe schedule progress.

        Ramp limits are computed from start_date on every request, so this opt-in helper is
        intentionally lightweight and safe to skip in tests.
        """
        while stop is None or not stop.is_set():
            for domain, config in self.repository.list_configs():
                if config.enabled:
                    self.repository.upsert_config(domain, config, now=self._now())
            try:
                if stop is None:
                    await asyncio.sleep(interval_seconds)
                else:
                    await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    def _require_config(self, domain: str) -> WarmupConfig:
        config = self.repository.get_config(domain)
        if config is None or not config.enabled:
            raise ValueError(f"warmup is not enabled for {domain}")
        return config

    def _daily_limit_for(self, config: WarmupConfig, now: datetime) -> int:
        if config.ramp_days <= 1 or config.ramp_start_limit >= config.daily_limit:
            return config.daily_limit
        day = self._current_day(config, now)
        if day >= config.ramp_days:
            return config.daily_limit
        step = (config.daily_limit - config.ramp_start_limit) / max(1, config.ramp_days - 1)
        return min(config.daily_limit, max(1, math.ceil(config.ramp_start_limit + (day - 1) * step)))

    def _current_day(self, config: WarmupConfig, now: datetime) -> int:
        start = config.start_date or now
        return max(1, (now.date() - start.date()).days + 1)

    def _next_batch_at(self, domain: str, daily_remaining: int, hourly_remaining: int) -> datetime:
        now = self._now()
        if daily_remaining <= 0:
            oldest = self.repository.oldest_executed_since(domain, now - timedelta(hours=24))
            return (oldest + timedelta(hours=24)) if oldest else now + timedelta(hours=24)
        if hourly_remaining <= 0:
            oldest = self.repository.oldest_executed_since(domain, now - timedelta(hours=1))
            return (oldest + timedelta(hours=1)) if oldest else now + timedelta(hours=1)
        return now

    def _block_reason(
        self,
        requested: int,
        allowed: int,
        daily_remaining: int,
        hourly_remaining: int,
        max_per_batch: int,
    ) -> str:
        if allowed <= 0 and daily_remaining <= 0:
            return "daily warmup limit reached"
        if allowed <= 0 and hourly_remaining <= 0:
            return "hourly warmup limit reached"
        if requested > max_per_batch:
            return f"batch exceeds warmup max_per_batch {max_per_batch}"
        return f"batch exceeds remaining warmup capacity {allowed}"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _log(self, severity: str, message: str, **context: Any) -> None:
        if self.logger is not None:
            self.logger.log(LogComponent.WARMUP, severity, message, **context)


def max_per_batch_or_default(value: int | None, config: WarmupConfig) -> int:
    """Return an override batch size or existing config batch size."""
    return max(1, int(value or config.max_per_batch))


def start_warmup_scheduler(service: WarmupService, *, interval_seconds: float = 3600.0) -> asyncio.Task[None]:
    """Start the opt-in warmup scheduler in a running event loop."""
    stop = asyncio.Event()
    task = asyncio.create_task(service.run_ramp_scheduler(interval_seconds=interval_seconds, stop=stop))
    setattr(task, "warmup_stop_event", stop)
    return task


async def stop_warmup_scheduler(task: asyncio.Task[None] | None) -> None:
    """Stop a scheduler task created by start_warmup_scheduler."""
    if task is None:
        return
    stop = getattr(task, "warmup_stop_event", None)
    if stop is not None:
        stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
