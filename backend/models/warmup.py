"""Warmup models for sender-domain ramp-up enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class WarmupEventType(str, Enum):
    """Warmup event types stored in the append-only event ledger."""

    SCHEDULED = "WarmupScheduled"
    EXECUTED = "WarmupExecuted"
    OVERRIDE = "WarmupOverride"


@dataclass(slots=True)
class WarmupConfig:
    """Per-domain warmup limits and ramp-up schedule."""

    daily_limit: int = 100
    max_per_batch: int = 25
    max_per_hour: int = 20
    ramp_start_limit: int = 10
    ramp_days: int = 7
    enabled: bool = True
    start_date: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the configuration for API and UI consumers."""
        return {
            "daily_limit": self.daily_limit,
            "max_per_batch": self.max_per_batch,
            "max_per_hour": self.max_per_hour,
            "ramp_start_limit": self.ramp_start_limit,
            "ramp_days": self.ramp_days,
            "enabled": self.enabled,
            "start_date": self.start_date.isoformat() if self.start_date else None,
        }


@dataclass(slots=True)
class WarmupStatus:
    """Current warmup progress for one sender domain."""

    domain: str
    current_day: int
    daily_limit: int
    sent_today: int
    sent_this_hour: int
    remaining_today: int
    remaining_this_hour: int
    remaining_capacity: int
    max_per_batch: int
    next_batch_at: datetime | None
    throttled: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize the status for API and UI consumers."""
        return {
            "domain": self.domain,
            "current_day": self.current_day,
            "daily_limit": self.daily_limit,
            "sent_today": self.sent_today,
            "sent_this_hour": self.sent_this_hour,
            "remaining_today": self.remaining_today,
            "remaining_this_hour": self.remaining_this_hour,
            "remaining_capacity": self.remaining_capacity,
            "max_per_batch": self.max_per_batch,
            "next_batch_at": self.next_batch_at.isoformat() if self.next_batch_at else None,
            "throttled": self.throttled,
        }
