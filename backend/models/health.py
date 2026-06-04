"""Health monitor models for backend, domain, queue, and server status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    """Traffic-light health states surfaced by the monitor."""

    OK = "green"
    WARN = "yellow"
    CRITICAL = "red"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ComponentHealth:
    """Health status for one monitored component."""

    name: str
    kind: str
    status: HealthStatus
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    last_checked: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the component for API and UI consumers."""
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status.value,
            "detail": self.detail,
            "metrics": self.metrics,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


@dataclass(slots=True)
class HealthServer:
    """Configurable SMTP, MX, VPS, or proxy health target."""

    id: str
    host: str
    kind: str
    port: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize server configuration."""
        return {
            "id": self.id,
            "host": self.host,
            "kind": self.kind,
            "port": self.port,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ServerHealth(ComponentHealth):
    """Health status for one configured server target."""

    server_id: str = ""
    host: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize server health with target identity."""
        data = ComponentHealth.to_dict(self)
        data.update({"server_id": self.server_id, "host": self.host})
        return data


@dataclass(slots=True)
class QueueDepth:
    """Current queued and processing depth across messages and recipients."""

    queued_messages: int = 0
    processing_messages: int = 0
    queued_recipients: int = 0
    processing_recipients: int = 0

    @property
    def total(self) -> int:
        """Return combined active message and recipient depth."""
        return self.queued_messages + self.processing_messages + self.queued_recipients + self.processing_recipients

    def to_dict(self) -> dict[str, int]:
        """Serialize queue depth metrics."""
        return {
            "queued_messages": self.queued_messages,
            "processing_messages": self.processing_messages,
            "queued_recipients": self.queued_recipients,
            "processing_recipients": self.processing_recipients,
            "total": self.total,
        }


@dataclass(slots=True)
class DomainHealthSummary:
    """DKIM/SPF/DMARC health summary for one sending domain."""

    domain: str
    status: HealthStatus
    health_score: int
    dkim_verified: bool
    spf_verified: bool
    dmarc_verified: bool
    detail: str = ""
    last_checked: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize domain health for API and UI consumers."""
        return {
            "domain": self.domain,
            "status": self.status.value,
            "health_score": self.health_score,
            "dkim_verified": self.dkim_verified,
            "spf_verified": self.spf_verified,
            "dmarc_verified": self.dmarc_verified,
            "detail": self.detail,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }
