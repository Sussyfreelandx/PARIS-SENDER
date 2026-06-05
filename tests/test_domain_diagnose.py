"""Tests for DNS provider detection and deep domain diagnosis."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.models import RecordType
from backend.repositories import DomainRepository, LedgerRepository, LogRepository
from backend.services import DeliverabilityService, DomainService, LoggingService, detect_dns_provider
from tests.conftest import FakeResolver


def _service(resolver: FakeResolver) -> DomainService:
    return DomainService(DomainRepository(":memory:"), resolver=resolver)


def test_detect_dns_provider_matches_cloudflare() -> None:
    resolver = FakeResolver(nameservers=["dana.ns.cloudflare.com", "rick.ns.cloudflare.com"])
    info = detect_dns_provider("example.com", resolver)
    assert info.provider == "Cloudflare"
    assert info.nameservers
    assert "Cloudflare" in info.guidance


def test_detect_dns_provider_matches_godaddy_and_warns_about_suffix() -> None:
    resolver = FakeResolver(nameservers=["ns01.domaincontrol.com", "ns02.domaincontrol.com"])
    info = detect_dns_provider("example.com", resolver)
    assert info.provider == "GoDaddy"
    assert "WITHOUT" in info.guidance


def test_detect_dns_provider_unknown_when_no_nameservers() -> None:
    info = detect_dns_provider("example.com", FakeResolver())
    assert info.provider == "Unknown"
    assert info.nameservers == []


def test_detect_dns_provider_handles_resolver_without_ns_method() -> None:
    class TxtOnly:
        def resolve_txt(self, host: str) -> list[str]:
            return []

    info = detect_dns_provider("example.com", TxtOnly())
    assert info.provider == "Unknown"


def test_diagnose_domain_reports_failing_dkim_and_dmarc_with_hints() -> None:
    # SPF published, DKIM/DMARC missing — mirrors the user's reported scenario.
    domains = _service(FakeResolver(nameservers=["ns1.domaincontrol.com"]))
    domain = domains.add_domain("example.com")
    spf_host = f"{domain.name}"
    domains.resolver.records = {spf_host: ["v=spf1 a mx ~all"]}

    report = domains.diagnose_domain(domain.id)

    assert report["provider"]["provider"] == "GoDaddy"
    by_type = {record["record_type"]: record for record in report["records"]}
    assert by_type[RecordType.SPF.value]["verified"] is True
    assert by_type[RecordType.DKIM.value]["verified"] is False
    assert by_type[RecordType.DMARC.value]["verified"] is False
    # Hints are actionable and reference the exact host to publish at.
    assert "_domainkey" in by_type[RecordType.DKIM.value]["hint"]
    assert "_dmarc" in by_type[RecordType.DMARC.value]["host"]
    assert report["failing_count"] == 2
    assert report["verified_count"] == 1


def test_diagnose_domain_detects_existing_dkim_regardless_of_key() -> None:
    # DKIM is detected by the existence of a valid published record at the
    # selector — the user's own provider key — not by matching this app's
    # generated key. So any valid DKIM record verifies.
    domains = _service(FakeResolver())
    domain = domains.add_domain("example.com")
    records = {r.host: [r.value] for r in domains.required_records(domain)}
    dkim_host = next(r.host for r in domains.required_records(domain) if r.record_type is RecordType.DKIM)
    records[dkim_host] = ["v=DKIM1; k=rsa; p=SOMEOTHERPROVIDERKEYDATA"]
    domains.resolver.records = records

    report = domains.diagnose_domain(domain.id)
    by_type = {record["record_type"]: record for record in report["records"]}
    dkim = by_type[RecordType.DKIM.value]
    assert dkim["verified"] is True
    assert dkim["detected_selector"] == domain.dkim_selector


def _diagnose_client() -> tuple[TestClient, int]:
    repo = LedgerRepository(":memory:")
    resolver = FakeResolver(nameservers=["ns1.example-dns.net"])
    domains = DomainService(DomainRepository(":memory:"), resolver=resolver)
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, threshold=0, logger=logger),
        logging_service=logger,
    )
    client = TestClient(app)
    domain_id = client.post("/domains", json={"name": "example.com"}).json()["id"]
    return client, domain_id


def test_diagnose_endpoint_returns_report() -> None:
    client, domain_id = _diagnose_client()
    response = client.post(f"/domains/{domain_id}/diagnose")
    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "example.com"
    assert "provider" in body
    assert len(body["records"]) == 3


def test_diagnose_endpoint_404_for_unknown_domain() -> None:
    client, _ = _diagnose_client()
    assert client.post("/domains/999999/diagnose").status_code == 404
