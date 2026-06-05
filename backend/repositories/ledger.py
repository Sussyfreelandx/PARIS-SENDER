"""SQLite-backed transactional email ledger repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import Campaign, Event, EventType, Message, Recipient, Status

ConnectionFactory = Callable[[], sqlite3.Connection]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dt_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


class LedgerRepository:
    """Repository hiding SQL details behind a Postgres-ready ledger interface."""

    def __init__(self, database: str | Path = "ledger.sqlite3", connection_factory: ConnectionFactory | None = None) -> None:
        self.database = str(database)
        self._connection_factory = connection_factory or self._default_connection_factory
        self._connection = self._connection_factory()
        self._connection.row_factory = sqlite3.Row
        self.create_schema()

    def _default_connection_factory(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, check_same_thread=False)

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the repository's single connection."""
        return self._connection

    def create_schema(self) -> None:
        """Create ledger tables if they do not already exist."""
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(campaign_id, email)
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    recipient_id INTEGER NOT NULL REFERENCES recipients(id),
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    recipient_id INTEGER NOT NULL REFERENCES recipients(id),
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT,
                    error TEXT,
                    url TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )

    def create_campaign(self, campaign: Campaign | str, metadata: dict[str, Any] | None = None) -> Campaign:
        """Persist and return a campaign."""
        campaign_obj = campaign if isinstance(campaign, Campaign) else Campaign(name=campaign, metadata=metadata or {})
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO campaigns (name, created_at, metadata) VALUES (?, ?, ?)",
                (campaign_obj.name, _dt_to_text(campaign_obj.created_at), json.dumps(campaign_obj.metadata)),
            )
        campaign_obj.id = int(cursor.lastrowid)
        return campaign_obj

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        """Fetch a campaign by id."""
        row = self.connection.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            return None
        return Campaign(id=row["id"], name=row["name"], created_at=_dt_from_text(row["created_at"]), metadata=json.loads(row["metadata"]))

    def list_campaigns(self) -> list[Campaign]:
        """Return all campaigns, most recently created first."""
        rows = self.connection.execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()
        return [
            Campaign(
                id=row["id"],
                name=row["name"],
                created_at=_dt_from_text(row["created_at"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def delete_campaign(self, campaign_id: int) -> bool:
        """Delete a campaign and all of its recipients, messages, and events.

        Returns ``True`` when a campaign was removed and ``False`` when no
        campaign matched the given id.
        """
        with self.connection:
            exists = self.connection.execute(
                "SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if exists is None:
                return False
            self.connection.execute("DELETE FROM events WHERE campaign_id = ?", (campaign_id,))
            self.connection.execute("DELETE FROM messages WHERE campaign_id = ?", (campaign_id,))
            self.connection.execute("DELETE FROM recipients WHERE campaign_id = ?", (campaign_id,))
            self.connection.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        return True

    def add_recipient(self, campaign_id: int, email: str, metadata: dict[str, Any] | None = None) -> Recipient:
        """Add or return a recipient for a campaign."""
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO recipients (campaign_id, email, status, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (campaign_id, email, Status.QUEUED.value, _dt_to_text(now), json.dumps(metadata or {})),
            )
        row = self.connection.execute(
            "SELECT * FROM recipients WHERE campaign_id = ? AND email = ?", (campaign_id, email)
        ).fetchone()
        return self._recipient_from_row(row)

    def create_message(self, message: Message) -> Message:
        """Persist and return a message."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO messages (campaign_id, recipient_id, subject, content, status, provider_message_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.campaign_id,
                    message.recipient_id,
                    message.subject,
                    message.content,
                    message.status.value,
                    message.provider_message_id,
                    _dt_to_text(message.created_at),
                    _dt_to_text(message.updated_at),
                ),
            )
        message.id = int(cursor.lastrowid)
        return message

    def get_message(self, message_id: int) -> Message | None:
        """Fetch a message by id."""
        row = self.connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._message_from_row(row) if row else None

    def list_messages_for_campaign(self, campaign_id: int) -> list[Message]:
        """Return all messages for a campaign."""
        rows = self.connection.execute("SELECT * FROM messages WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        return [self._message_from_row(row) for row in rows]

    def record_event(self, event: Event) -> Event:
        """Persist an event and update message/recipient status rollups."""
        message = self.get_message(event.message_id)
        if message is None:
            raise ValueError(f"message {event.message_id} does not exist")
        event.campaign_id = event.campaign_id or message.campaign_id
        event.recipient_id = event.recipient_id or message.recipient_id
        now = event.created_at
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO events (campaign_id, recipient_id, message_id, event_type, status, provider_message_id, error, url, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.campaign_id,
                    event.recipient_id,
                    event.message_id,
                    event.event_type.value,
                    event.status.value,
                    event.provider_message_id,
                    event.error,
                    event.url,
                    json.dumps(event.metadata),
                    _dt_to_text(now),
                ),
            )
            self.connection.execute(
                "UPDATE messages SET status = ?, provider_message_id = COALESCE(?, provider_message_id), updated_at = ? WHERE id = ?",
                (event.status.value, event.provider_message_id, _dt_to_text(_utc_now()), event.message_id),
            )
            self.connection.execute(
                "UPDATE recipients SET status = ? WHERE id = ?",
                (event.status.value, event.recipient_id),
            )
        event.id = int(cursor.lastrowid)
        return event

    def list_events_for_message(self, message_id: int) -> list[Event]:
        """Return events for a message in persistence order."""
        rows = self.connection.execute("SELECT * FROM events WHERE message_id = ? ORDER BY id", (message_id,)).fetchall()
        return [self._event_from_row(row) for row in rows]

    def recipient_status_rollups(self, campaign_id: int) -> dict[Status, int]:
        """Count recipients by their current status for one campaign."""
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM recipients WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        rollups = {status: 0 for status in Status}
        for row in rows:
            rollups[Status(row["status"])] = int(row["count"])
        return rollups

    def status_counts(self, *, table: str = "messages") -> dict[Status, int]:
        """Count current statuses across all messages or recipients."""
        if table not in {"messages", "recipients"}:
            raise ValueError("table must be 'messages' or 'recipients'")
        rows = self.connection.execute(f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status").fetchall()
        counts = {status: 0 for status in Status}
        for row in rows:
            counts[Status(row["status"])] = int(row["count"])
        return counts

    def event_status_counts_since(self, since: datetime) -> dict[Status, int]:
        """Count ledger event statuses since an inclusive timestamp."""
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM events WHERE created_at >= ? GROUP BY status",
            (_dt_to_text(since),),
        ).fetchall()
        counts = {status: 0 for status in Status}
        for row in rows:
            counts[Status(row["status"])] = int(row["count"])
        return counts

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def _recipient_from_row(self, row: sqlite3.Row) -> Recipient:
        return Recipient(
            id=row["id"],
            campaign_id=row["campaign_id"],
            email=row["email"],
            status=Status(row["status"]),
            created_at=_dt_from_text(row["created_at"]),
            metadata=json.loads(row["metadata"]),
        )

    def _message_from_row(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            campaign_id=row["campaign_id"],
            recipient_id=row["recipient_id"],
            subject=row["subject"],
            content=row["content"],
            status=Status(row["status"]),
            provider_message_id=row["provider_message_id"],
            created_at=_dt_from_text(row["created_at"]),
            updated_at=_dt_from_text(row["updated_at"]),
        )

    def _event_from_row(self, row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            campaign_id=row["campaign_id"],
            recipient_id=row["recipient_id"],
            message_id=row["message_id"],
            event_type=EventType(row["event_type"]),
            status=Status(row["status"]),
            provider_message_id=row["provider_message_id"],
            error=row["error"],
            url=row["url"],
            metadata=json.loads(row["metadata"]),
            created_at=_dt_from_text(row["created_at"]),
        )
