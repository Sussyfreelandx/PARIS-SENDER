"""Compatibility exports for the transactional email ledger."""

from backend.models import Campaign, Event, EventType, Message, Recipient, Status
from backend.repositories.ledger import LedgerRepository

__all__ = ["Campaign", "Event", "EventType", "LedgerRepository", "Message", "Recipient", "Status"]
