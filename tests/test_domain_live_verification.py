"""Part 3 audit: domain verification must be live-DNS with a precise result shape.

Pins that the live verification report reflects real DNS lookups (A/NS/MX + the
SPF/DKIM/DMARC TXT checks), detects the provider, stamps verification_source as
``live_dns``, and explains every failing check -- never a placeholder state.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.repositories import DomainRepository, LedgerRepository, LogRepository
from backend.services import DeliverabilityService, DomainService, LoggingService
from tests.conftest import FakeResolver


def _service(resolver: FakeResolver) -> DomainService:
    return DomainService(DomainRepository(":memory:"), resolver=resolver)


def _fully_published(domain_name: str, selector: str, public_key: str) -> dict[str, list[str]]:
    return {
        domain_name: ["v=spf1 a mx ~all"],
        f"{selector}._domainkey.{domain_name}": [f"v=DKIM1; k=rsa; p={public_key}"],
        f"_dmarc.{domain_name}": ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"],
    }


def test_live_report_all_valid_uses_live_dns_source() -> None:
    resolver = FakeResolver(
        nameservers=["dana.ns.cloudflare.com"],
        mx={"example.com": ["mx1.example.com"]},
        a={"example.com": ["203.0.113.7"]},
    )
    domains = _service(resolver)
    domain = domains.add_domain("example.com")
    resolver.records = _fully_published("example.com", domain.dkim_selector, domain.dkim_public_key)

    report = domains.live_verification_report(domain.id)

    assert report["domain"] == "example.com"
    assert report["dns_resolves"] is True
    assert report["mx_present"] is True
    assert report["a_present"] is True
    assert report["spf_valid"] is True
    assert report["dkim_valid"] is True
    assert report["dmarc_valid"] is True
    assert report["provider_detected"] == "Cloudflare"
    assert report["verification_source"] == "live_dns"
    assert report["verification_timestamp"]
    assert report["errors"] == {}
    assert report["verification_strength"] == "strong"


def test_live_report_verification_strength_partial_and_failed() -> None:
    # No auth records published at all -> "failed".
    resolver = FakeResolver(nameservers=["ns1.domaincontrol.com"])
    domains = _service(resolver)
    domain = domains.add_domain("example.com")
    assert domains.live_verification_report(domain.id)["verification_strength"] == "failed"

    # Publish only SPF (one of three) -> "partial".
    resolver.records = {"example.com": ["v=spf1 a mx ~all"]}
    assert domains.live_verification_report(domain.id)["verification_strength"] == "partial"


def test_live_report_failures_include_precise_explanations() -> None:
    # Domain resolves (has NS) but publishes no SPF/DKIM/DMARC and no MX.
    resolver = FakeResolver(nameservers=["ns1.domaincontrol.com"])
    domains = _service(resolver)
    domain = domains.add_domain("example.com")

    report = domains.live_verification_report(domain.id)

    assert report["dns_resolves"] is True  # NS present
    assert report["mx_present"] is False
    assert report["spf_valid"] is False
    assert report["dkim_valid"] is False
    assert report["dmarc_valid"] is False
    assert "mx_present" in report["errors"]
    assert "spf_valid" in report["errors"]
    assert "dkim_valid" in report["errors"]
    assert "dmarc_valid" in report["errors"]
    assert report["verification_source"] == "live_dns"


def test_live_report_endpoint_returns_shape() -> None:
    repo = LedgerRepository(":memory:")
    resolver = FakeResolver(nameservers=["ns1.example.net"], mx={"example.com": ["mx.example.com"]})
    domains = _service(resolver)
    logger = LoggingService(LogRepository(":memory:"))
    app = create_app(
        repository=repo,
        domain_service=domains,
        deliverability_service=DeliverabilityService(repo, domains, logger=logger),
        logging_service=logger,
    )
    client = TestClient(app)
    domain = domains.add_domain("example.com")

    response = client.post(f"/domains/{domain.id}/verify/live")
    body = response.json()

    assert response.status_code == 200
    for key in ("domain", "dns_resolves", "mx_present", "spf_valid", "dkim_valid", "dmarc_valid",
                "provider_detected", "verification_timestamp", "verification_source"):
        assert key in body
    assert body["verification_source"] == "live_dns"
    assert client.post("/domains/999999/verify/live").status_code == 404
