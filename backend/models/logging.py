"""Centralized backend logging models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class LogSeverity(str, Enum):
    """Severity levels for centralized backend log entries."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogComponent(str, Enum):
    """Backend components that emit centralized log entries."""

    API = "API"
    AUTOGRAB = "AUTOGRAB"
    CAMPAIGN = "CAMPAIGN"
    DELIVERABILITY = "DELIVERABILITY"
    DELIVERY = "DELIVERY"
    HEALTH = "HEALTH"
    WARMUP = "WARMUP"


@dataclass(slots=True)
class LogEntry:
    """Structured centralized log entry for API, analytics, and UI consumers."""

    id: int | None
    timestamp: datetime
    severity: LogSeverity
    component: LogComponent
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the log entry into a JSON-friendly shape."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
            "component": self.component.value,
            "message": self.message,
            "context": self.context,
        }
