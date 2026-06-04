"""SQLite repository for centralized backend logs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import LogComponent, LogEntry, LogSeverity

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


class LogRepository:
    """Persist centralized backend log entries in SQLite."""

    def __init__(
        self,
        database: str | Path = ":memory:",
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
        """Create centralized log tables and indexes."""
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_logs_component ON logs(component)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_logs_severity ON logs(severity)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")

    def append(
        self,
        entry: LogEntry | None = None,
        *,
        timestamp: datetime | None = None,
        severity: LogSeverity | str | None = None,
        component: LogComponent | str | None = None,
        message: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a log entry and return its JSON-friendly stored representation."""
        if entry is not None:
            timestamp = entry.timestamp
            severity = entry.severity
            component = entry.component
            message = entry.message
            context = entry.context
        if severity is None or component is None or message is None:
            raise ValueError("severity, component, and message are required")
        timestamp = timestamp or _utc_now()
        severity_value = self._severity_value(severity)
        component_value = self._component_value(component)
        payload = context or {}
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO logs (timestamp, severity, component, message, context)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_dt_to_text(timestamp), severity_value, component_value, message, json.dumps(payload)),
            )
        return {
            "id": int(cursor.lastrowid),
            "timestamp": _dt_to_text(timestamp),
            "severity": severity_value,
            "component": component_value,
            "message": message,
            "context": payload,
        }

    def query(
        self,
        *,
        severity: LogSeverity | str | None = None,
        component: LogComponent | str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return log entries filtered by severity, component, and timestamp range newest-first."""
        clauses: list[str] = []
        params: list[Any] = []
        if severity is not None:
            clauses.append("severity = ?")
            params.append(self._severity_value(severity))
        if component is not None:
            clauses.append("component = ?")
            params.append(self._component_value(component))
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(_dt_to_text(since))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(_dt_to_text(until))
        sql = "SELECT * FROM logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        """Return aggregate log counts by severity and component plus timestamp bounds."""
        total_row = self.connection.execute("SELECT COUNT(*) AS count, MIN(timestamp) AS earliest, MAX(timestamp) AS latest FROM logs").fetchone()
        severity_rows = self.connection.execute("SELECT severity, COUNT(*) AS count FROM logs GROUP BY severity").fetchall()
        component_rows = self.connection.execute("SELECT component, COUNT(*) AS count FROM logs GROUP BY component").fetchall()
        return {
            "total": int(total_row["count"] if total_row else 0),
            "by_severity": {row["severity"]: int(row["count"]) for row in severity_rows},
            "by_component": {row["component"]: int(row["count"]) for row in component_rows},
            "earliest_timestamp": total_row["earliest"] if total_row else None,
            "latest_timestamp": total_row["latest"] if total_row else None,
        }

    def archive(self, before: datetime) -> int:
        """Delete logs older than the cutoff and return the archived count."""
        cutoff = _dt_to_text(before)
        row = self.connection.execute("SELECT COUNT(*) AS count FROM logs WHERE timestamp < ?", (cutoff,)).fetchone()
        archived = int(row["count"] if row else 0)
        with self.connection:
            self.connection.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff,))
        return archived

    def count(self) -> int:
        """Return total live log count."""
        row = self.connection.execute("SELECT COUNT(*) AS count FROM logs").fetchone()
        return int(row["count"] if row else 0)

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "severity": row["severity"],
            "component": row["component"],
            "message": row["message"],
            "context": json.loads(row["context"]),
        }

    def _severity_value(self, value: LogSeverity | str) -> str:
        return value.value if isinstance(value, LogSeverity) else str(value).strip().upper()

    def _component_value(self, value: LogComponent | str) -> str:
        return value.value if isinstance(value, LogComponent) else str(value).strip().upper()
