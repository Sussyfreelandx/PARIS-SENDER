"""Tests for provider-aware verification fields and schema hardening."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models import Domain, DomainStatus
from backend.repositories import DomainRepository


def _table_columns(repo: DomainRepository) -> set[str]:
    return {row["name"] for row in repo.connection.execute("PRAGMA table_info(domains)").fetchall()}


def _index_names(repo: DomainRepository) -> set[str]:
    return {row["name"] for row in repo.connection.execute("PRAGMA index_list(domains)").fetchall()}


def test_schema_has_provider_columns_and_indexes() -> None:
    repo = DomainRepository(":memory:")
    columns = _table_columns(repo)
    for column in (
        "provider_name",
        "provider_verified",
        "provider_domain_id",
        "provider_status",
        "provider_last_checked",
        "sending_enabled",
    ):
        assert column in columns

    indexes = _index_names(repo)
    assert "idx_domains_name" in indexes
    assert "idx_domains_provider_status" in indexes
    assert "idx_domains_provider_verified" in indexes


def test_provider_fields_round_trip() -> None:
    repo = DomainRepository(":memory:")
    checked = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    domain = repo.create(
        Domain(
            name="example.com",
            provider_name="Postmark",
            provider_verified=True,
            provider_domain_id="prov-123",
            provider_status="active",
            provider_last_checked=checked,
        )
    )

    fetched = repo.get(domain.id or 0)
    assert fetched is not None
    assert fetched.provider_name == "Postmark"
    assert fetched.provider_verified is True
    assert fetched.provider_domain_id == "prov-123"
    assert fetched.provider_status == "active"
    assert fetched.provider_last_checked == checked


def test_sending_enabled_requires_dns_and_provider() -> None:
    domain = Domain(
        name="example.com",
        spf_verified=True,
        dkim_verified=True,
        dmarc_verified=True,
        provider_verified=False,
    )
    assert domain.sending_enabled is False

    domain.provider_verified = True
    assert domain.sending_enabled is True

    domain.dmarc_verified = False
    assert domain.sending_enabled is False


def test_sending_enabled_persisted_column_tracks_eligibility() -> None:
    repo = DomainRepository(":memory:")
    domain = repo.create(
        Domain(
            name="ready.example",
            spf_verified=True,
            dkim_verified=True,
            dmarc_verified=True,
            provider_verified=True,
        )
    )
    stored = repo.connection.execute(
        "SELECT sending_enabled FROM domains WHERE id = ?", (domain.id,)
    ).fetchone()[0]
    assert stored == 1


def test_health_history_deduplicates_unchanged_rows() -> None:
    repo = DomainRepository(":memory:")
    domain = repo.create(Domain(name="example.com", status=DomainStatus.PENDING, health_score=0))
    # Re-persisting without a status/score change must not add a history row.
    repo.update(domain)
    repo.update(domain)
    assert len(repo.health_history(domain.id or 0)) == 1

    domain.status = DomainStatus.VERIFIED
    domain.health_score = 100
    repo.update(domain)
    history = repo.health_history(domain.id or 0)
    assert len(history) == 2
    assert history[-1]["health_score"] == 100


def test_migration_adds_columns_to_legacy_table() -> None:
    repo = DomainRepository(":memory:")
    # Simulate a pre-provider installation by dropping the new columns is not
    # possible in SQLite; instead build a legacy table then re-run migration.
    repo.connection.execute("DROP TABLE domains")
    repo.connection.execute(
        """
        CREATE TABLE domains (
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
    repo.connection.execute(
        "INSERT INTO domains (name, status, dkim_selector, created_at, updated_at) "
        "VALUES ('legacy.example', 'PENDING', 'paris', '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')"
    )

    repo._migrate_schema()
    repo._create_indexes()

    fetched = repo.get_by_name("legacy.example")
    assert fetched is not None
    assert fetched.provider_verified is False
    assert fetched.provider_name is None
    assert fetched.sending_enabled is False
