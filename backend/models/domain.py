"""Typed domain entities for DKIM/SPF/DMARC lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DomainStatus(str, Enum):
    """Lifecycle states for a sending domain."""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class RecordType(str, Enum):
    """The DNS authentication record types managed for a domain."""

    DKIM = "DKIM"
    SPF = "SPF"
    DMARC = "DMARC"


@dataclass(slots=True)
class DnsRecord:
    """A required DNS record the operator must publish for a domain."""

    record_type: RecordType
    host: str
    value: str
    verified: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for the API/UI."""
        return {
            "record_type": self.record_type.value,
            "host": self.host,
            "value": self.value,
            "verified": self.verified,
            "error": self.error,
        }


@dataclass(slots=True)
class Domain:
    """A sending domain with its generated keys, records, and health."""

    name: str
    id: int | None = None
    status: DomainStatus = DomainStatus.PENDING
    dkim_selector: str = "paris"
    dkim_private_key: str | None = None
    dkim_public_key: str | None = None
    spf_record: str | None = None
    dmarc_record: str | None = None
    dmarc_policy: str = "none"
    health_score: int = 0
    dkim_verified: bool = False
    spf_verified: bool = False
    dmarc_verified: bool = False
    last_checked_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        """A domain is verified once every required record passes DNS checks."""
        return self.status is DomainStatus.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        """Serialize the domain to a JSON-friendly dict for the API/UI."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "dkim_selector": self.dkim_selector,
            "dkim_public_key": self.dkim_public_key,
            "spf_record": self.spf_record,
            "dmarc_record": self.dmarc_record,
            "dmarc_policy": self.dmarc_policy,
            "health_score": self.health_score,
            "dkim_verified": self.dkim_verified,
            "spf_verified": self.spf_verified,
            "dmarc_verified": self.dmarc_verified,
            "is_verified": self.is_verified,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }
