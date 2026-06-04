"""Tests for the Phase 4 domain repository and DKIM/SPF/DMARC service."""

from __future__ import annotations

import pytest

from backend.models import DomainStatus, RecordType
from backend.repositories import DomainRepository
from backend.services import DomainService
from backend.services.domain import (
    DomainError,
    build_dkim_record,
    build_dmarc_record,
    build_spf_record,
    generate_dkim_keypair,
    is_valid_domain,
)


class FakeResolver:
    """In-memory TXT resolver for deterministic verification tests."""

    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self.records = records or {}

    def resolve_txt(self, host: str) -> list[str]:
        return self.records.get(host, [])


def _service(resolver: FakeResolver | None = None) -> DomainService:
    return DomainService(DomainRepository(":memory:"), resolver=resolver)


def test_is_valid_domain():
    assert is_valid_domain("example.com")
    assert is_valid_domain("mail.example.co.uk")
    assert not is_valid_domain("not a domain")
    assert not is_valid_domain("example")
    assert not is_valid_domain("")


def test_generate_dkim_keypair_produces_pem_and_dns_key():
    keypair = generate_dkim_keypair("sel")
    assert "BEGIN PRIVATE KEY" in keypair.private_key_pem
    assert keypair.selector == "sel"
    assert len(keypair.public_key_dns) > 100


def test_record_builders():
    dkim = build_dkim_record("example.com", "paris", "ABC123")
    assert dkim.host == "paris._domainkey.example.com"
    assert "v=DKIM1" in dkim.value and "p=ABC123" in dkim.value

    spf = build_spf_record("example.com", includes=["spf.provider.net"])
    assert spf.host == "example.com"
    assert spf.value.startswith("v=spf1") and "include:spf.provider.net" in spf.value

    dmarc = build_dmarc_record("example.com", policy="reject")
    assert dmarc.host == "_dmarc.example.com"
    assert "p=reject" in dmarc.value


def test_add_domain_persists_and_generates_records():
    service = _service()
    domain = service.add_domain("Example.COM")
    assert domain.id is not None
    assert domain.name == "example.com"
    assert domain.status is DomainStatus.PENDING
    assert domain.dkim_private_key and domain.dkim_public_key
    assert domain.spf_record and domain.dmarc_record

    fetched = service.get_domain(domain.id)
    assert fetched is not None
    assert fetched.name == "example.com"


def test_add_domain_rejects_invalid_and_duplicate():
    service = _service()
    with pytest.raises(DomainError):
        service.add_domain("nope")
    service.add_domain("example.com")
    with pytest.raises(DomainError):
        service.add_domain("example.com")


def test_required_records_returns_three_types():
    service = _service()
    domain = service.add_domain("example.com")
    types = {r.record_type for r in service.required_records(domain)}
    assert types == {RecordType.DKIM, RecordType.SPF, RecordType.DMARC}


def test_verify_domain_all_records_present():
    service = _service()
    domain = service.add_domain("example.com")
    records = {r.host: [r.value] for r in service.required_records(domain)}
    service.resolver = FakeResolver(records)

    verified = service.verify_domain(domain.id)
    assert verified.dkim_verified and verified.spf_verified and verified.dmarc_verified
    assert verified.status is DomainStatus.VERIFIED
    assert verified.health_score == 100
    assert verified.last_checked_at is not None


def test_verify_domain_missing_records_fails():
    service = _service(FakeResolver({}))
    domain = service.add_domain("example.com")
    verified = service.verify_domain(domain.id)
    assert verified.status is DomainStatus.FAILED
    assert verified.health_score == 0
    assert not verified.dkim_verified


def test_verify_domain_partial_health_score():
    service = _service()
    domain = service.add_domain("example.com")
    records = {r.host: [r.value] for r in service.required_records(domain) if r.record_type is not RecordType.DMARC}
    service.resolver = FakeResolver(records)

    verified = service.verify_domain(domain.id)
    assert verified.dkim_verified and verified.spf_verified
    assert not verified.dmarc_verified
    assert verified.status is DomainStatus.FAILED
    assert verified.health_score == 70


def test_is_domain_verified_helper():
    service = _service()
    domain = service.add_domain("example.com")
    assert not service.is_domain_verified("example.com")
    records = {r.host: [r.value] for r in service.required_records(domain)}
    service.resolver = FakeResolver(records)
    service.verify_domain(domain.id)
    assert service.is_domain_verified("example.com")
    assert not service.is_domain_verified("unknown.com")


def test_regenerate_dkim_resets_verification():
    service = _service()
    domain = service.add_domain("example.com")
    original_key = domain.dkim_public_key
    rotated = service.regenerate_dkim(domain.id, selector="newsel")
    assert rotated.dkim_selector == "newsel"
    assert rotated.dkim_public_key != original_key
    assert not rotated.dkim_verified


def test_update_dmarc_policy():
    service = _service()
    domain = service.add_domain("example.com")
    updated = service.update_dmarc_policy(domain.id, "reject")
    assert updated.dmarc_policy == "reject"
    assert "p=reject" in updated.dmarc_record
    with pytest.raises(DomainError):
        service.update_dmarc_policy(domain.id, "bogus")


def test_delete_domain():
    service = _service()
    domain = service.add_domain("example.com")
    assert service.delete_domain(domain.id) is True
    assert service.get_domain(domain.id) is None


def test_health_history_recorded():
    repo = DomainRepository(":memory:")
    service = DomainService(repo)
    domain = service.add_domain("example.com")
    records = {r.host: [r.value] for r in service.required_records(domain)}
    service.resolver = FakeResolver(records)
    service.verify_domain(domain.id)
    history = repo.health_history(domain.id)
    assert len(history) >= 2
    assert history[-1]["health_score"] == 100


def test_list_domains():
    service = _service()
    service.add_domain("a-example.com")
    service.add_domain("b-example.com")
    names = [d.name for d in service.list_domains()]
    assert names == ["a-example.com", "b-example.com"]
