"""Typed email ledger entities used by the backend services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Canonical lifecycle states for campaign recipients and messages."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    OPENED = "OPENED"
    CLICKED = "CLICKED"
    BOUNCED = "BOUNCED"
    FAILED = "FAILED"
    UNSUBSCRIBED = "UNSUBSCRIBED"


class EventType(str, Enum):
    """Ledger event subtypes for analytics and tracking."""

    STATUS = "STATUS"
    BOUNCE = "BOUNCE"
    OPEN = "OPEN"
    CLICK = "CLICK"
    UNSUBSCRIBE = "UNSUBSCRIBE"


@dataclass(slots=True)
class Campaign:
    """A delivery campaign grouping recipients, messages, and events."""

    name: str
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Recipient:
    """A campaign recipient and their current rollup status."""

    campaign_id: int
    email: str
    id: int | None = None
    status: Status = Status.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Message:
    """An outbound message addressed to a single recipient."""

    campaign_id: int
    recipient_id: int
    subject: str
    content: str
    id: int | None = None
    status: Status = Status.QUEUED
    provider_message_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class Event:
    """A persisted ledger event for a message."""

    message_id: int
    status: Status
    event_type: EventType = EventType.STATUS
    id: int | None = None
    recipient_id: int | None = None
    campaign_id: int | None = None
    provider_message_id: str | None = None
    error: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def bounce(cls, message_id: int, error: str | None = None, **kwargs: Any) -> "Event":
        """Create a bounce event."""
        return cls(message_id=message_id, status=Status.BOUNCED, event_type=EventType.BOUNCE, error=error, **kwargs)

    @classmethod
    def open(cls, message_id: int, **kwargs: Any) -> "Event":
        """Create an open event."""
        return cls(message_id=message_id, status=Status.OPENED, event_type=EventType.OPEN, **kwargs)

    @classmethod
    def click(cls, message_id: int, url: str | None = None, **kwargs: Any) -> "Event":
        """Create a click event."""
        return cls(message_id=message_id, status=Status.CLICKED, event_type=EventType.CLICK, url=url, **kwargs)

    @classmethod
    def unsubscribe(cls, message_id: int, **kwargs: Any) -> "Event":
        """Create an unsubscribe event."""
        return cls(message_id=message_id, status=Status.UNSUBSCRIBED, event_type=EventType.UNSUBSCRIBE, **kwargs)
