"""Tests for encrypted DKIM private keys at rest."""

from __future__ import annotations

from backend.models import Domain, DomainStatus
from backend.repositories import DomainRepository
from backend.services import DomainService, SecurityService


def test_domain_repository_encrypts_dkim_private_key_at_rest() -> None:
    security = SecurityService(keys=["domain-test-key"])
    repo = DomainRepository(":memory:", security_service=security)
    service = DomainService(repo)

    domain = service.add_domain("example.com")
    raw = repo.connection.execute("SELECT dkim_private_key FROM domains WHERE id = ?", (domain.id,)).fetchone()[0]
    fetched = service.get_domain(domain.id or 0)

    assert raw.startswith(SecurityService.TOKEN_PREFIX)
    assert "BEGIN PRIVATE KEY" not in raw
    assert fetched is not None
    assert fetched.dkim_private_key == domain.dkim_private_key


def test_domain_repository_loads_legacy_plaintext_dkim_key() -> None:
    repo = DomainRepository(":memory:", security_service=SecurityService(keys=["domain-test-key"]))
    legacy = Domain(name="legacy.example", status=DomainStatus.PENDING, dkim_private_key="legacy plaintext")
    with repo.connection:
        repo.connection.execute(
            """
            INSERT INTO domains (
                name, status, dkim_selector, dkim_private_key, dkim_public_key, spf_record,
                dmarc_record, dmarc_policy, health_score, dkim_verified, spf_verified,
                dmarc_verified, last_checked_at, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            repo._to_params(legacy)[:3] + ("legacy plaintext",) + repo._to_params(legacy)[4:],
        )

    fetched = repo.get_by_name("legacy.example")

    assert fetched is not None
    assert fetched.dkim_private_key == "legacy plaintext"
