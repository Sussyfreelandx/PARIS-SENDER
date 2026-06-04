"""SQLite-backed repository for sending domains and their DKIM/SPF/DMARC state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from backend.models import Domain, DomainStatus

if TYPE_CHECKING:
    from backend.services.security import SecurityService

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


class DomainRepository:
    """Persist sending domains, their generated keys, and verification state."""

    def __init__(
        self,
        database: str | Path = "domains.sqlite3",
        connection_factory: ConnectionFactory | None = None,
        security_service: SecurityService | None = None,
    ) -> None:
        self.database = str(database)
        self._connection_factory = connection_factory or self._default_connection_factory
        self._security_service = security_service
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
        """Create the domains table if it does not already exist."""
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS domains (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    dkim_selector TEXT NOT NULL,
                    dkim_private_key TEXT,
                    dkim_public_key TEXT,
                    spf_record TEXT,
                    dmarc_record TEXT,
                    dmarc_policy TEXT NOT NULL DEFAULT 'none',
                    health_score INTEGER NOT NULL DEFAULT 0,
                    dkim_verified INTEGER NOT NULL DEFAULT 0,
                    spf_verified INTEGER NOT NULL DEFAULT 0,
                    dmarc_verified INTEGER NOT NULL DEFAULT 0,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS domain_health_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_id INTEGER NOT NULL REFERENCES domains(id),
                    health_score INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    def create(self, domain: Domain) -> Domain:
        """Persist and return a new domain."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO domains (
                    name, status, dkim_selector, dkim_private_key, dkim_public_key,
                    spf_record, dmarc_record, dmarc_policy, health_score,
                    dkim_verified, spf_verified, dmarc_verified,
                    last_checked_at, created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_params(domain),
            )
        domain.id = int(cursor.lastrowid)
        self._record_health(domain)
        return domain

    def update(self, domain: Domain) -> Domain:
        """Persist changes to an existing domain."""
        if domain.id is None:
            raise ValueError("domain must have an id to be updated")
        domain.updated_at = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE domains SET
                    name = ?, status = ?, dkim_selector = ?, dkim_private_key = ?,
                    dkim_public_key = ?, spf_record = ?, dmarc_record = ?, dmarc_policy = ?,
                    health_score = ?, dkim_verified = ?, spf_verified = ?, dmarc_verified = ?,
                    last_checked_at = ?, created_at = ?, updated_at = ?, metadata = ?
                WHERE id = ?
                """,
                (*self._to_params(domain), domain.id),
            )
        self._record_health(domain)
        return domain

    def get(self, domain_id: int) -> Domain | None:
        """Fetch a domain by id."""
        row = self.connection.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_by_name(self, name: str) -> Domain | None:
        """Fetch a domain by its name."""
        row = self.connection.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return self._from_row(row) if row else None

    def list(self) -> list[Domain]:
        """Return all domains ordered by name."""
        rows = self.connection.execute("SELECT * FROM domains ORDER BY name").fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, domain_id: int) -> bool:
        """Delete a domain and its health history; return True if a row was removed."""
        with self.connection:
            self.connection.execute("DELETE FROM domain_health_history WHERE domain_id = ?", (domain_id,))
            cursor = self.connection.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        return cursor.rowcount > 0

    def health_history(self, domain_id: int) -> list[dict[str, object]]:
        """Return the recorded health-score history for a domain."""
        rows = self.connection.execute(
            "SELECT health_score, status, recorded_at FROM domain_health_history WHERE domain_id = ? ORDER BY id",
            (domain_id,),
        ).fetchall()
        return [
            {"health_score": row["health_score"], "status": row["status"], "recorded_at": row["recorded_at"]}
            for row in rows
        ]

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def _record_health(self, domain: Domain) -> None:
        if domain.id is None:
            return
        with self.connection:
            self.connection.execute(
                "INSERT INTO domain_health_history (domain_id, health_score, status, recorded_at) VALUES (?, ?, ?, ?)",
                (domain.id, domain.health_score, domain.status.value, _dt_to_text(_utc_now())),
            )

    @property
    def security_service(self) -> SecurityService:
        """Return the encryption service used for DKIM secrets."""
        if self._security_service is None:
            from backend.services.security import SecurityService

            self._security_service = SecurityService()
        return self._security_service

    def _encrypt_dkim_key(self, value: str | None) -> str | None:
        if value is None or self.security_service.is_encrypted(value):
            return value
        return self.security_service.encrypt(value)

    def _decrypt_dkim_key(self, value: str | None) -> str | None:
        return self.security_service.decrypt_or_plaintext(value)

    def _to_params(self, domain: Domain) -> tuple[object, ...]:
        return (
            domain.name,
            domain.status.value,
            domain.dkim_selector,
            self._encrypt_dkim_key(domain.dkim_private_key),
            domain.dkim_public_key,
            domain.spf_record,
            domain.dmarc_record,
            domain.dmarc_policy,
            domain.health_score,
            int(domain.dkim_verified),
            int(domain.spf_verified),
            int(domain.dmarc_verified),
            _dt_to_text(domain.last_checked_at) if domain.last_checked_at else None,
            _dt_to_text(domain.created_at),
            _dt_to_text(domain.updated_at),
            json.dumps(domain.metadata),
        )

    def _from_row(self, row: sqlite3.Row) -> Domain:
        return Domain(
            id=row["id"],
            name=row["name"],
            status=DomainStatus(row["status"]),
            dkim_selector=row["dkim_selector"],
            dkim_private_key=self._decrypt_dkim_key(row["dkim_private_key"]),
            dkim_public_key=row["dkim_public_key"],
            spf_record=row["spf_record"],
            dmarc_record=row["dmarc_record"],
            dmarc_policy=row["dmarc_policy"],
            health_score=row["health_score"],
            dkim_verified=bool(row["dkim_verified"]),
            spf_verified=bool(row["spf_verified"]),
            dmarc_verified=bool(row["dmarc_verified"]),
            last_checked_at=_dt_from_text(row["last_checked_at"]),
            created_at=_dt_from_text(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_dt_from_text(row["updated_at"]),  # type: ignore[arg-type]
            metadata=json.loads(row["metadata"]),
        )
