"""Domain lifecycle service: DKIM/SPF/DMARC generation, DNS verification, health."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.models import DnsRecord, Domain, DomainStatus, RecordType
from backend.repositories.domain import DomainRepository

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9-]{1,63}(?<!-)\.)+[A-Za-z]{2,63}$"
)

# Health weighting per authentication record (sums to 100).
_DKIM_WEIGHT = 40
_SPF_WEIGHT = 30
_DMARC_WEIGHT = 30


def is_valid_domain(name: str) -> bool:
    """Return True if ``name`` is a syntactically valid DNS domain name."""
    return bool(_DOMAIN_RE.match(name.strip().lower()))


class DnsResolver(Protocol):
    """Resolver seam so DNS lookups can be faked in tests."""

    def resolve_txt(self, host: str) -> list[str]:
        """Return the TXT record strings published at ``host``."""
        ...


class DnspythonResolver:
    """Default resolver backed by dnspython."""

    def resolve_txt(self, host: str) -> list[str]:
        import dns.resolver  # imported lazily so the dependency is optional in tests

        try:
            answers = dns.resolver.resolve(host, "TXT")
        except Exception:
            return []
        records: list[str] = []
        for rdata in answers:
            parts = getattr(rdata, "strings", None)
            if parts is not None:
                records.append(b"".join(parts).decode("utf-8", "ignore"))
            else:
                records.append(str(rdata).strip('"'))
        return records


@dataclass(slots=True)
class DkimKeyPair:
    """A generated DKIM key pair (PEM private key, DNS-ready public key)."""

    selector: str
    private_key_pem: str
    public_key_dns: str


def generate_dkim_keypair(selector: str = "paris", key_size: int = 2048) -> DkimKeyPair:
    """Generate an RSA DKIM key pair and the base64 public key for the DNS TXT record."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_b64 = base64.b64encode(public_der).decode("ascii")
    return DkimKeyPair(selector=selector, private_key_pem=private_pem, public_key_dns=public_b64)


def build_dkim_record(domain: str, selector: str, public_key_dns: str) -> DnsRecord:
    """Build the DKIM DNS TXT record for a domain."""
    return DnsRecord(
        record_type=RecordType.DKIM,
        host=f"{selector}._domainkey.{domain}",
        value=f"v=DKIM1; k=rsa; p={public_key_dns}",
    )


def build_spf_record(domain: str, includes: Iterable[str] | None = None) -> DnsRecord:
    """Build the SPF DNS TXT record for a domain."""
    mechanisms = ["v=spf1", "a", "mx"]
    for include in includes or []:
        mechanisms.append(f"include:{include}")
    mechanisms.append("~all")
    return DnsRecord(record_type=RecordType.SPF, host=domain, value=" ".join(mechanisms))


def build_dmarc_record(domain: str, policy: str = "none", rua: str | None = None) -> DnsRecord:
    """Build the DMARC DNS TXT record for a domain."""
    rua_address = rua or f"dmarc@{domain}"
    value = f"v=DMARC1; p={policy}; rua=mailto:{rua_address}; fo=1; adkim=s; aspf=s"
    return DnsRecord(record_type=RecordType.DMARC, host=f"_dmarc.{domain}", value=value)


class DomainError(Exception):
    """Raised for invalid domain operations (bad name, duplicate, missing)."""


class DomainService:
    """Coordinates domain onboarding, DNS record generation, and verification."""

    def __init__(self, repository: DomainRepository, resolver: DnsResolver | None = None) -> None:
        self.repository = repository
        self.resolver = resolver or DnspythonResolver()

    def add_domain(
        self,
        name: str,
        *,
        selector: str = "paris",
        dmarc_policy: str = "none",
        spf_includes: Iterable[str] | None = None,
    ) -> Domain:
        """Onboard a domain: validate, generate DKIM keys and SPF/DMARC records."""
        normalized = name.strip().lower()
        if not is_valid_domain(normalized):
            raise DomainError(f"invalid domain name: {name!r}")
        if self.repository.get_by_name(normalized) is not None:
            raise DomainError(f"domain already exists: {normalized!r}")

        keypair = generate_dkim_keypair(selector)
        spf = build_spf_record(normalized, spf_includes)
        dmarc = build_dmarc_record(normalized, dmarc_policy)
        dkim = build_dkim_record(normalized, selector, keypair.public_key_dns)

        domain = Domain(
            name=normalized,
            status=DomainStatus.PENDING,
            dkim_selector=selector,
            dkim_private_key=keypair.private_key_pem,
            dkim_public_key=keypair.public_key_dns,
            spf_record=spf.value,
            dmarc_record=dmarc.value,
            dmarc_policy=dmarc_policy,
        )
        domain.metadata["dkim_host"] = dkim.host
        return self.repository.create(domain)

    def required_records(self, domain: Domain) -> list[DnsRecord]:
        """Return the DNS records the operator must publish, with verification flags."""
        dkim = build_dkim_record(domain.name, domain.dkim_selector, domain.dkim_public_key or "")
        dkim.verified = domain.dkim_verified
        spf = build_spf_record(domain.name)
        spf.value = domain.spf_record or spf.value
        spf.verified = domain.spf_verified
        dmarc = build_dmarc_record(domain.name, domain.dmarc_policy)
        dmarc.value = domain.dmarc_record or dmarc.value
        dmarc.verified = domain.dmarc_verified
        return [dkim, spf, dmarc]

    def regenerate_dkim(self, domain_id: int, selector: str | None = None) -> Domain:
        """Rotate a domain's DKIM key pair and reset its verification state."""
        domain = self._require(domain_id)
        new_selector = selector or domain.dkim_selector
        keypair = generate_dkim_keypair(new_selector)
        domain.dkim_selector = new_selector
        domain.dkim_private_key = keypair.private_key_pem
        domain.dkim_public_key = keypair.public_key_dns
        domain.dkim_verified = False
        domain.metadata["dkim_host"] = build_dkim_record(domain.name, new_selector, keypair.public_key_dns).host
        self._apply_status(domain)
        return self.repository.update(domain)

    def update_dmarc_policy(self, domain_id: int, policy: str) -> Domain:
        """Update the DMARC policy (none/quarantine/reject) and rebuild the record."""
        if policy not in {"none", "quarantine", "reject"}:
            raise DomainError(f"invalid DMARC policy: {policy!r}")
        domain = self._require(domain_id)
        domain.dmarc_policy = policy
        domain.dmarc_record = build_dmarc_record(domain.name, policy).value
        domain.dmarc_verified = False
        self._apply_status(domain)
        return self.repository.update(domain)

    def delete_domain(self, domain_id: int) -> bool:
        """Delete a domain by id."""
        return self.repository.delete(domain_id)

    def verify_domain(self, domain_id: int) -> Domain:
        """Run DNS checks for DKIM/SPF/DMARC and update status and health score."""
        domain = self._require(domain_id)
        records = self.required_records(domain)
        for record in records:
            record.verified, record.error = self._check_record(record)
            if record.record_type is RecordType.DKIM:
                domain.dkim_verified = record.verified
            elif record.record_type is RecordType.SPF:
                domain.spf_verified = record.verified
            elif record.record_type is RecordType.DMARC:
                domain.dmarc_verified = record.verified
        domain.last_checked_at = datetime.now(timezone.utc)
        self._apply_status(domain)
        return self.repository.update(domain)

    def health_score(self, domain: Domain) -> int:
        """Compute a 0-100 health score from verified records and DMARC strictness."""
        score = 0
        if domain.dkim_verified:
            score += _DKIM_WEIGHT
        if domain.spf_verified:
            score += _SPF_WEIGHT
        if domain.dmarc_verified:
            score += _DMARC_WEIGHT
        return min(score, 100)

    def list_domains(self) -> list[Domain]:
        """Return all known domains."""
        return self.repository.list()

    def get_domain(self, domain_id: int) -> Domain | None:
        """Return a domain by id, or None."""
        return self.repository.get(domain_id)

    def is_domain_verified(self, name: str) -> bool:
        """Return True if a domain exists and is fully verified."""
        domain = self.repository.get_by_name(name.strip().lower())
        return bool(domain and domain.is_verified)

    def get_domain_by_name(self, name: str) -> Domain | None:
        """Return a managed domain by name, or None if it is not managed."""
        return self.repository.get_by_name(name.strip().lower())

    def _apply_status(self, domain: Domain) -> None:
        domain.health_score = self.health_score(domain)
        if domain.dkim_verified and domain.spf_verified and domain.dmarc_verified:
            domain.status = DomainStatus.VERIFIED
        elif domain.last_checked_at is not None:
            domain.status = DomainStatus.FAILED
        else:
            domain.status = DomainStatus.PENDING

    def _check_record(self, record: DnsRecord) -> tuple[bool, str | None]:
        try:
            published = self.resolver.resolve_txt(record.host)
        except Exception as exc:  # pragma: no cover - defensive
            return False, str(exc)
        if not published:
            return False, "no TXT record published"
        if record.record_type is RecordType.DKIM:
            expected = self._dkim_public_token(record.value)
            for entry in published:
                if "v=dkim1" in entry.lower() and (not expected or expected in entry.replace(" ", "")):
                    return True, None
            return False, "DKIM record not found or key mismatch"
        if record.record_type is RecordType.SPF:
            for entry in published:
                if entry.lower().startswith("v=spf1"):
                    return True, None
            return False, "SPF record not found"
        for entry in published:
            if entry.lower().startswith("v=dmarc1"):
                return True, None
        return False, "DMARC record not found"

    @staticmethod
    def _dkim_public_token(value: str) -> str:
        match = re.search(r"p=([A-Za-z0-9+/=]+)", value)
        return match.group(1) if match else ""

    def _require(self, domain_id: int) -> Domain:
        domain = self.repository.get(domain_id)
        if domain is None:
            raise DomainError(f"domain {domain_id} not found")
        return domain
