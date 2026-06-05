"""Domain lifecycle service: DKIM/SPF/DMARC generation, DNS verification, health."""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable, Iterable
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


# Public DNS resolvers queried (in addition to the system default) so a freshly
# published record is found as soon as any major resolver has it, instead of
# waiting for the operator's local resolver cache to expire. Aggregating across
# resolvers is what gives the auto-scan its high hit rate during propagation.
_PUBLIC_DNS_RESOLVERS: tuple[tuple[str, ...], ...] = (
    ("8.8.8.8", "8.8.4.4"),  # Google
    ("1.1.1.1", "1.0.0.1"),  # Cloudflare
    ("9.9.9.9",),  # Quad9
)

# Per-query bound (seconds). Kept short so an unreachable resolver can never hang
# the scan; the aggregate stays fast because resolvers are tried independently.
_DNS_QUERY_TIMEOUT = 3.0


class DnspythonResolver:
    """Default resolver backed by dnspython.

    Queries the system resolver first and then a set of public resolvers,
    aggregating and de-duplicating the TXT strings found. Every lookup is bounded
    by an explicit timeout so a slow or unreachable name server can never make the
    verification scan hang.
    """

    def _query_txt(self, resolver: object, host: str) -> list[str]:
        import dns.resolver  # imported lazily so the dependency is optional in tests

        try:
            answers = resolver.resolve(  # type: ignore[attr-defined]
                host, "TXT", lifetime=_DNS_QUERY_TIMEOUT
            )
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

    def resolve_txt(self, host: str) -> list[str]:
        import dns.resolver  # imported lazily so the dependency is optional in tests

        seen: set[str] = set()
        aggregated: list[str] = []

        def _absorb(values: list[str]) -> None:
            for value in values:
                if value not in seen:
                    seen.add(value)
                    aggregated.append(value)

        # System resolver first (honours any local/split-horizon DNS).
        try:
            default_resolver = dns.resolver.Resolver()
            default_resolver.timeout = _DNS_QUERY_TIMEOUT
            default_resolver.lifetime = _DNS_QUERY_TIMEOUT
            _absorb(self._query_txt(default_resolver, host))
        except Exception:
            pass

        # Then public resolvers, so propagation on any major resolver is detected.
        for nameservers in _PUBLIC_DNS_RESOLVERS:
            try:
                public_resolver = dns.resolver.Resolver(configure=False)
                public_resolver.nameservers = list(nameservers)
                public_resolver.timeout = _DNS_QUERY_TIMEOUT
                public_resolver.lifetime = _DNS_QUERY_TIMEOUT
                _absorb(self._query_txt(public_resolver, host))
            except Exception:
                continue

        return aggregated

    def resolve_ns(self, host: str) -> list[str]:
        """Return the authoritative name servers for ``host`` (lower-cased, no trailing dot)."""
        import dns.resolver  # imported lazily so the dependency is optional in tests

        try:
            answers = dns.resolver.resolve(host, "NS", lifetime=_DNS_QUERY_TIMEOUT)
        except Exception:
            return []
        servers: list[str] = []
        for rdata in answers:
            target = str(getattr(rdata, "target", rdata)).strip().rstrip(".").lower()
            if target:
                servers.append(target)
        return servers


# Ordered (most specific first) signatures mapping a name-server substring to the
# DNS provider that operates it. Used to detect where a domain's DNS is hosted so
# the UI can show provider-specific instructions for publishing DKIM/SPF/DMARC.
_DNS_PROVIDER_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("cloudflare", "Cloudflare"),
    ("awsdns", "AWS Route 53"),
    ("domaincontrol.com", "GoDaddy"),
    ("googledomains.com", "Google Domains"),
    ("registrar-servers.com", "Namecheap"),
    ("namecheaphosting.com", "Namecheap"),
    ("dnsmadeeasy.com", "DNS Made Easy"),
    ("digitalocean.com", "DigitalOcean"),
    ("azure-dns", "Azure DNS"),
    ("nsone.net", "NS1"),
    ("dns.he.net", "Hurricane Electric"),
    ("name.com", "Name.com"),
    ("gandi.net", "Gandi"),
    ("ovh.net", "OVH"),
    ("bluehost.com", "Bluehost"),
    ("hostgator.com", "HostGator"),
    ("dreamhost.com", "DreamHost"),
    ("wpengine.com", "WP Engine"),
    ("squarespacedns.com", "Squarespace"),
    ("wixdns.net", "Wix"),
    ("shopify.com", "Shopify"),
    ("vercel-dns.com", "Vercel"),
    ("nsone.net", "NS1"),
    ("linode.com", "Linode"),
    ("hetzner.com", "Hetzner"),
    ("zoneedit.com", "ZoneEdit"),
    ("googledomains", "Google Domains"),
    ("google.com", "Google Cloud DNS"),
)

# Providers that historically auto-append the domain to a TXT host name. For these
# the operator must enter the host WITHOUT the trailing domain to avoid a doubled
# name like ``selector._domainkey.example.com.example.com``.
_HOST_SUFFIX_STRIPPING_PROVIDERS = frozenset({"GoDaddy", "Namecheap", "Bluehost", "HostGator", "Cloudflare"})


@dataclass(slots=True)
class DnsProviderInfo:
    """Detected DNS provider for a domain plus actionable publishing guidance."""

    provider: str
    nameservers: list[str]
    guidance: str

    def to_dict(self) -> dict[str, object]:
        return {"provider": self.provider, "nameservers": list(self.nameservers), "guidance": self.guidance}


def detect_dns_provider(domain: str, resolver: object | None = None) -> DnsProviderInfo:
    """Detect which DNS provider hosts ``domain`` by inspecting its name servers.

    Returns the matched provider name (or ``"Unknown"``), the resolved name
    servers, and provider-specific guidance the operator can follow to publish
    the DKIM/SPF/DMARC records correctly. The resolver only needs an optional
    ``resolve_ns`` method; if it is missing or returns nothing the provider is
    reported as ``"Unknown"`` without raising.
    """
    normalized = domain.strip().lower().rstrip(".")
    nameservers: list[str] = []
    resolve_ns = getattr(resolver, "resolve_ns", None) if resolver is not None else None
    if callable(resolve_ns):
        try:
            nameservers = list(resolve_ns(normalized))
        except Exception:  # noqa: BLE001 - detection must never crash verification
            nameservers = []

    provider = "Unknown"
    for ns in nameservers:
        host = ns.lower()
        for signature, name in _DNS_PROVIDER_SIGNATURES:
            if signature in host:
                provider = name
                break
        if provider != "Unknown":
            break

    return DnsProviderInfo(provider=provider, nameservers=nameservers, guidance=_provider_guidance(provider))


def _provider_guidance(provider: str) -> str:
    if provider == "Unknown":
        return (
            "Could not identify the DNS provider automatically. Add the records in the DNS "
            "zone editor at your registrar/host. For TXT records, enter the host exactly as "
            "shown and paste the full value on one line."
        )
    base = f"DNS appears to be hosted at {provider}. "
    if provider in _HOST_SUFFIX_STRIPPING_PROVIDERS:
        return base + (
            "When adding TXT records there, enter only the host/name portion WITHOUT the "
            "trailing domain (e.g. use 'selector._domainkey' and '_dmarc', not the full "
            "name) because the provider appends the domain automatically. Paste the full "
            "record value unbroken."
        )
    return base + (
        "Add each record in its DNS editor using the exact host shown and paste the full "
        "value on a single line (some editors split long DKIM keys automatically)."
    )


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

    def auto_verify_domain(
        self,
        domain_id: int,
        *,
        attempts: int = 3,
        interval: float = 2.0,
        sleeper: Callable[[float], None] | None = None,
    ) -> Domain:
        """Repeatedly scan DKIM/SPF/DMARC until the domain verifies or attempts run out.

        DNS records published moments earlier may not be visible on the first
        lookup, so this retries the scan a small, bounded number of times (with a
        short pause between tries) and stops as soon as every record matches. The
        attempt count and interval are bounded so the call always returns quickly
        and never hangs, while the multi-resolver lookups keep the match rate high.
        """
        wait = sleeper or time.sleep
        bounded_attempts = max(1, min(int(attempts), 6))
        bounded_interval = max(0.0, min(float(interval), 10.0))
        domain = self.verify_domain(domain_id)
        for remaining in range(bounded_attempts - 1):
            if domain.is_verified:
                break
            if bounded_interval:
                wait(bounded_interval)
            domain = self.verify_domain(domain_id)
        return domain


    def diagnose_domain(self, domain_id: int) -> dict[str, object]:
        """Run a deep, accurate per-record DNS search with provider-aware guidance.

        This re-runs the DKIM/SPF/DMARC checks (persisting the refreshed status)
        and, for every record, returns the host queried, the expected value, the
        values actually published, whether it matched, and a precise, actionable
        hint when it does not. It also detects the domain's DNS provider so the
        UI can show provider-specific publishing instructions. This is what turns
        an opaque "DKIM/DMARC failing" into a concrete fix.
        """
        domain = self.verify_domain(domain_id)
        provider = detect_dns_provider(domain.name, self.resolver)
        record_reports: list[dict[str, object]] = []
        for record in self.required_records(domain):
            record_reports.append(self._diagnose_record(record, provider))
        verified = [r for r in record_reports if r["verified"]]
        failing = [r for r in record_reports if not r["verified"]]
        summary = (
            "All authentication records are published correctly."
            if not failing
            else "Failing: " + ", ".join(str(r["record_type"]) for r in failing)
        )
        return {
            "domain": domain.name,
            "status": domain.status.value,
            "health_score": domain.health_score,
            "provider": provider.to_dict(),
            "records": record_reports,
            "summary": summary,
            "verified_count": len(verified),
            "failing_count": len(failing),
        }

    def _diagnose_record(self, record: DnsRecord, provider: DnsProviderInfo) -> dict[str, object]:
        try:
            published = self.resolver.resolve_txt(record.host)
        except Exception:  # noqa: BLE001 - defensive; report a generic message instead of crashing
            published = []
            lookup_error: str | None = f"DNS lookup failed for '{record.host}'."
        else:
            lookup_error = None
        verified, error = self._check_record(record, published=published, lookup_error=lookup_error)
        hint = "" if verified else self._record_hint(record, published, provider)
        return {
            "record_type": record.record_type.value,
            "host": record.host,
            "expected": record.value,
            "published": list(published),
            "verified": verified,
            "error": error,
            "hint": hint,
        }

    def _record_hint(self, record: DnsRecord, published: list[str], provider: DnsProviderInfo) -> str:
        suffix = f" {provider.guidance}" if provider.provider != "Unknown" else ""
        if not published:
            if record.record_type is RecordType.DKIM:
                return (
                    f"No TXT record was found at '{record.host}'. Publish the DKIM key at this exact "
                    f"host (selector '{record.host.split('._domainkey.')[0]}')." + suffix
                )
            if record.record_type is RecordType.DMARC:
                return f"No TXT record was found at '{record.host}'. Publish the DMARC record at this exact host." + suffix
            return f"No TXT record was found at '{record.host}'. Publish the SPF record at the domain root." + suffix

        if record.record_type is RecordType.DKIM:
            expected_token = self._dkim_public_token(record.value)
            has_dkim = any("v=dkim1" in entry.lower() for entry in published)
            if not has_dkim:
                return (
                    f"A TXT record exists at '{record.host}' but none start with 'v=DKIM1'. It may be the "
                    "wrong selector or a different record — replace it with the DKIM value shown." + suffix
                )
            if expected_token and not any(expected_token in entry.replace(" ", "") for entry in published):
                return (
                    "A DKIM record is published but its public key does not match this domain's key. The "
                    "value was likely truncated or split into multiple strings — re-paste the full 'p=' "
                    "key on one line, or rotate DKIM and republish." + suffix
                )
            return "DKIM record found but did not match; re-publish the exact value shown." + suffix
        if record.record_type is RecordType.SPF:
            return (
                f"TXT records exist at '{record.host}' but none start with 'v=spf1'. Add an SPF record "
                "beginning with 'v=spf1' (keep only one SPF record per domain)." + suffix
            )
        return (
            f"TXT records exist at '{record.host}' but none start with 'v=DMARC1'. Ensure the DMARC record "
            "is published at exactly this host and begins with 'v=DMARC1'." + suffix
        )

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

    def _check_record(
        self,
        record: DnsRecord,
        *,
        published: list[str] | None = None,
        lookup_error: str | None = None,
    ) -> tuple[bool, str | None]:
        if published is None:
            try:
                published = self.resolver.resolve_txt(record.host)
            except Exception:  # pragma: no cover - defensive
                return False, f"DNS lookup failed for '{record.host}'."
        if lookup_error is not None:
            return False, lookup_error
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
