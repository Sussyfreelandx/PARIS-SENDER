"""SQLite repository for warmup configuration and append-only events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import WarmupConfig, WarmupEventType

ConnectionFactory = Callable[[], sqlite3.Connection]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dt_from_text(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class WarmupRepository:
    """Persist warmup domain settings and warmup send events."""

    def __init__(
        self,
        database: str | Path = "warmup.sqlite3",
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
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
        """Create warmup tables without modifying the ledger schema."""
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS warmup_domains (
                    domain TEXT PRIMARY KEY,
                    daily_limit INTEGER NOT NULL,
                    max_per_batch INTEGER NOT NULL,
                    max_per_hour INTEGER NOT NULL,
                    ramp_start_limit INTEGER NOT NULL,
                    ramp_days INTEGER NOT NULL,
                    start_date TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS warmup_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    campaign_id INTEGER,
                    batch_size INTEGER,
                    detail TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_warmup_events_domain_time ON warmup_events(domain, created_at)")

    def upsert_config(self, domain: str, config: WarmupConfig, *, now: datetime | None = None) -> WarmupConfig:
        """Create or replace a warmup config for a normalized domain."""
        normalized = self._normalize(domain)
        timestamp = now or _utc_now()
        start_date = config.start_date or self._existing_start_date(normalized) or timestamp
        stored = WarmupConfig(
            daily_limit=max(1, int(config.daily_limit)),
            max_per_batch=max(1, int(config.max_per_batch)),
            max_per_hour=max(1, int(config.max_per_hour)),
            ramp_start_limit=max(1, int(config.ramp_start_limit)),
            ramp_days=max(1, int(config.ramp_days)),
            enabled=bool(config.enabled),
            start_date=start_date,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO warmup_domains (
                    domain, daily_limit, max_per_batch, max_per_hour, ramp_start_limit,
                    ramp_days, start_date, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    daily_limit = excluded.daily_limit,
                    max_per_batch = excluded.max_per_batch,
                    max_per_hour = excluded.max_per_hour,
                    ramp_start_limit = excluded.ramp_start_limit,
                    ramp_days = excluded.ramp_days,
                    start_date = excluded.start_date,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    stored.daily_limit,
                    stored.max_per_batch,
                    stored.max_per_hour,
                    stored.ramp_start_limit,
                    stored.ramp_days,
                    _dt_to_text(stored.start_date),
                    int(stored.enabled),
                    _dt_to_text(timestamp),
                ),
            )
        return stored

    def get_config(self, domain: str) -> WarmupConfig | None:
        """Fetch warmup config for a domain."""
        row = self.connection.execute("SELECT * FROM warmup_domains WHERE domain = ?", (self._normalize(domain),)).fetchone()
        return self._config_from_row(row) if row else None

    def list_configs(self) -> list[tuple[str, WarmupConfig]]:
        """Return all warmup configs ordered by domain."""
        rows = self.connection.execute("SELECT * FROM warmup_domains ORDER BY domain").fetchall()
        return [(row["domain"], self._config_from_row(row)) for row in rows]

    def append_event(
        self,
        domain: str,
        event_type: WarmupEventType,
        *,
        campaign_id: int | None = None,
        batch_size: int | None = None,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a warmup event and return its JSON-friendly representation."""
        timestamp = created_at or _utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO warmup_events (domain, event_type, campaign_id, batch_size, detail, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._normalize(domain),
                    event_type.value,
                    campaign_id,
                    batch_size,
                    detail,
                    json.dumps(metadata or {}),
                    _dt_to_text(timestamp),
                ),
            )
        return {
            "id": int(cursor.lastrowid),
            "domain": self._normalize(domain),
            "event_type": event_type.value,
            "campaign_id": campaign_id,
            "batch_size": batch_size,
            "detail": detail,
            "metadata": metadata or {},
            "created_at": _dt_to_text(timestamp),
        }

    def count_executed_since(self, domain: str, since: datetime) -> int:
        """Count executed warmup sends for a domain since an inclusive timestamp."""
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(batch_size), 0) AS count
            FROM warmup_events
            WHERE domain = ? AND event_type = ? AND created_at >= ?
            """,
            (self._normalize(domain), WarmupEventType.EXECUTED.value, _dt_to_text(since)),
        ).fetchone()
        return int(row["count"] if row else 0)

    def oldest_executed_since(self, domain: str, since: datetime) -> datetime | None:
        """Return the oldest executed event timestamp inside a rolling window."""
        row = self.connection.execute(
            """
            SELECT MIN(created_at) AS created_at
            FROM warmup_events
            WHERE domain = ? AND event_type = ? AND created_at >= ?
            """,
            (self._normalize(domain), WarmupEventType.EXECUTED.value, _dt_to_text(since)),
        ).fetchone()
        return _dt_from_text(row["created_at"]) if row and row["created_at"] else None

    def list_events(self, domain: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent warmup events for a domain."""
        rows = self.connection.execute(
            """
            SELECT * FROM warmup_events
            WHERE domain = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (self._normalize(domain), max(1, int(limit))),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def _existing_start_date(self, domain: str) -> datetime | None:
        row = self.connection.execute("SELECT start_date FROM warmup_domains WHERE domain = ?", (domain,)).fetchone()
        return _dt_from_text(row["start_date"]) if row else None

    def _config_from_row(self, row: sqlite3.Row) -> WarmupConfig:
        return WarmupConfig(
            daily_limit=row["daily_limit"],
            max_per_batch=row["max_per_batch"],
            max_per_hour=row["max_per_hour"],
            ramp_start_limit=row["ramp_start_limit"],
            ramp_days=row["ramp_days"],
            enabled=bool(row["enabled"]),
            start_date=_dt_from_text(row["start_date"]),
        )

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "domain": row["domain"],
            "event_type": row["event_type"],
            "campaign_id": row["campaign_id"],
            "batch_size": row["batch_size"],
            "detail": row["detail"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
        }

    def _normalize(self, domain: str) -> str:
        return domain.strip().lower()
