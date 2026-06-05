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

    def resolve_a(self, host: str) -> list[str]:
        """Return the A/AAAA addresses published for ``host`` (live DNS)."""
        import dns.resolver  # imported lazily so the dependency is optional in tests

        addresses: list[str] = []
        for record_type in ("A", "AAAA"):
            try:
                answers = dns.resolver.resolve(host, record_type, lifetime=_DNS_QUERY_TIMEOUT)
            except Exception:
                continue
            for rdata in answers:
                value = str(getattr(rdata, "address", rdata)).strip()
                if value:
                    addresses.append(value)
        return addresses

    def resolve_mx(self, host: str) -> list[str]:
        """Return the mail exchangers for ``host``, best preference first (live DNS)."""
        import dns.resolver  # imported lazily so the dependency is optional in tests

        try:
            answers = dns.resolver.resolve(host, "MX", lifetime=_DNS_QUERY_TIMEOUT)
        except Exception:
            return []
        hosts = sorted(
            ((int(getattr(r, "preference", 0)), str(getattr(r, "exchange", r)).rstrip(".").lower()) for r in answers),
            key=lambda item: item[0],
        )
        return [host for _, host in hosts if host]


# Ordered (most specific first) signatures mapping a name-server substring to the
# DNS provider that operates it. Used to detect where a domain's DNS is hosted so
# the UI can show provider-specific instructions for publishing DKIM/SPF/DMARC.
_DNS_PROVIDER_SIGNATURES: tuple[tuple[str, str], ...] = (
    # Registrars / DNS hosts (most specific substrings first).
    ("cloudflare", "Cloudflare"),
    ("awsdns", "AWS Route 53"),
    ("domaincontrol.com", "GoDaddy"),
    ("godaddy", "GoDaddy"),
    ("registrar-servers.com", "Namecheap"),
    ("namecheaphosting.com", "Namecheap"),
    ("namecheap", "Namecheap"),
    ("googledomains.com", "Google Domains"),
    ("googledomains", "Google Domains"),
    ("domains.google", "Google Domains"),
    ("google.com", "Google Cloud DNS"),
    ("ns.cloudflare.com", "Cloudflare"),
    ("dnsmadeeasy.com", "DNS Made Easy"),
    ("digitalocean.com", "DigitalOcean"),
    ("azure-dns", "Azure DNS"),
    ("nsone.net", "NS1"),
    ("dns.he.net", "Hurricane Electric"),
    ("name.com", "Name.com"),
    ("gandi.net", "Gandi"),
    ("ovh.net", "OVH"),
    ("ovh.com", "OVH"),
    ("bluehost.com", "Bluehost"),
    ("hostgator.com", "HostGator"),
    ("dreamhost.com", "DreamHost"),
    ("wpengine.com", "WP Engine"),
    ("squarespacedns.com", "Squarespace"),
    ("wixdns.net", "Wix"),
    ("shopify.com", "Shopify"),
    ("vercel-dns.com", "Vercel"),
    ("linode.com", "Linode"),
    ("linodedns.com", "Linode"),
    ("hetzner.com", "Hetzner"),
    ("hetzner.de", "Hetzner"),
    ("zoneedit.com", "ZoneEdit"),
    # Expanded coverage so the large registrars/hosts most domains use are all
    # recognised (the previous short list missed many popular providers).
    ("ui-dns.", "IONOS"),
    ("ionos.", "IONOS"),
    ("1and1.", "IONOS"),
    ("hostinger.com", "Hostinger"),
    ("hostingerdns.com", "Hostinger"),
    ("porkbun.com", "Porkbun"),
    ("hover.com", "Hover"),
    ("dnsowl.com", "Hover"),
    ("namesilo.com", "NameSilo"),
    ("dynadot.com", "Dynadot"),
    ("enom.com", "Enom"),
    ("name-services.com", "Enom"),
    ("worldnic.com", "Network Solutions"),
    ("networksolutions.com", "Network Solutions"),
    ("register.com", "Register.com"),
    ("registereddomains.com", "Register.com"),
    ("domain.com", "Domain.com"),
    ("a2hosting.com", "A2 Hosting"),
    ("siteground.net", "SiteGround"),
    ("inmotionhosting.com", "InMotion Hosting"),
    ("greengeeks.com", "GreenGeeks"),
    ("messagingengine.com", "Fastmail"),
    ("fastmail.com", "Fastmail"),
    ("zoho.com", "Zoho"),
    ("zohocloud.com", "Zoho"),
    ("cloudns.net", "ClouDNS"),
    ("constellix.com", "Constellix"),
    ("ultradns.", "UltraDNS"),
    ("akam.net", "Akamai"),
    ("akamai", "Akamai"),
    ("fastly.net", "Fastly"),
    ("netlify.com", "Netlify"),
    ("nsone.net", "NS1"),
    ("rackspace.com", "Rackspace"),
    ("stabletransit.com", "Rackspace"),
    ("liquidweb.com", "Liquid Web"),
    ("mediatemple.net", "Media Temple"),
    ("weebly.com", "Weebly"),
    ("siteground.eu", "SiteGround"),
    ("ezoic.com", "Ezoic"),
    ("wordpress.com", "WordPress.com"),
    ("automattic.com", "WordPress.com"),
    ("kinsta.com", "Kinsta"),
    ("flywheel.com", "Flywheel"),
    ("cloudflare.net", "Cloudflare"),
    ("dns.com", "DNS.com"),
    ("yandex.net", "Yandex"),
    ("nic.ru", "RU-CENTER"),
    ("beget.com", "Beget"),
    ("timeweb.ru", "Timeweb"),
    ("reg.ru", "REG.RU"),
    ("transip.net", "TransIP"),
    ("openprovider.nl", "Openprovider"),
    ("combell.com", "Combell"),
    ("one.com", "One.com"),
    ("loopia.se", "Loopia"),
    ("inwx.de", "INWX"),
    ("infomaniak.com", "Infomaniak"),
    ("scaleway.com", "Scaleway"),
    ("online.net", "Scaleway"),
    ("vultr.com", "Vultr"),
    ("dns.he.net", "Hurricane Electric"),
)

# Providers that historically auto-append the domain to a TXT host name. For these
# the operator must enter the host WITHOUT the trailing domain to avoid a doubled
# name like ``selector._domainkey.example.com.example.com``.
_HOST_SUFFIX_STRIPPING_PROVIDERS = frozenset(
    {
        "GoDaddy",
        "Namecheap",
        "Bluehost",
        "HostGator",
        "Cloudflare",
        "IONOS",
        "Hostinger",
        "Network Solutions",
        "Register.com",
        "Domain.com",
        "A2 Hosting",
        "SiteGround",
        "InMotion Hosting",
        "GreenGeeks",
        "Hover",
        "NameSilo",
        "Dynadot",
        "Porkbun",
    }
)

# DKIM keys live at ``<selector>._domainkey.<domain>`` and every mail provider
# uses its own selector name. To detect an *existing* DKIM record with high
# accuracy (instead of only the key this app generated) the scan probes the
# domain's configured selector first and then this curated set of selectors used
# by the major email providers. Non-existent selectors return a fast NXDOMAIN, so
# scanning the list stays quick and the search stops at the first match.
_DKIM_COMMON_SELECTORS: tuple[str, ...] = (
    "default",          # cPanel / Namecheap / GoDaddy shared hosting, generic
    "google",           # Google Workspace
    "selector1",        # Microsoft 365
    "selector2",        # Microsoft 365 (rotation)
    "k1", "k2", "k3",   # Mailchimp / Mandrill, Mailgun
    "s1", "s2",         # SendGrid, generic
    "dkim",             # generic
    "mail",             # Brevo/Sendinblue, generic
    "email",            # generic
    "smtp",             # generic / Mailgun
    "mx",               # generic
    "zoho", "zmail",    # Zoho Mail
    "mandrill",         # Mandrill
    "mailgun", "mg",    # Mailgun
    "pm",               # Postmark
    "fm1", "fm2", "fm3", "mesmtp",  # Fastmail
    "protonmail", "protonmail2", "protonmail3",  # Proton Mail
    "s2048", "s1024",   # Yahoo / AOL
    "dk",               # ActiveCampaign / legacy DomainKeys
    "sig1",             # iCloud / Apple
    "scph0", "scph1",   # SparkPost
    "ctct1", "ctct2",   # Constant Contact
    "sib",              # Sendinblue/Brevo
    "everlytickey1", "everlytickey2",  # Everlytic
    "turbo-smtp",       # turboSMTP
    "key1", "key2",     # generic
    "selector",         # generic
    "amazonses",        # Amazon SES (legacy)
)

# Valid DMARC policy values, ordered from least to most strict. Used when the
# published policy is auto-detected from DNS so the stored policy always reflects
# what is actually published at the provider rather than a manually chosen value.
_DMARC_POLICIES = ("none", "quarantine", "reject")


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
        """Run DNS checks for DKIM/SPF/DMARC and update status and health score.

        DKIM is detected across the domain's configured selector plus the common
        selectors used by the major mail providers, so an *existing* DKIM record
        is matched even when this app did not generate it. The DMARC policy is
        read back from the published record so the stored policy always reflects
        what is actually published (it is never chosen manually)."""
        domain = self._require(domain_id)
        records = self.required_records(domain)
        for record in records:
            if record.record_type is RecordType.DKIM:
                verified, error, selector = self._verify_dkim(domain)
                record.verified, record.error = verified, error
                domain.dkim_verified = verified
                if verified and selector:
                    domain.metadata["dkim_detected_selector"] = selector
            elif record.record_type is RecordType.SPF:
                record.verified, record.error = self._check_record(record)
                domain.spf_verified = record.verified
            elif record.record_type is RecordType.DMARC:
                verified, error, policy, published_value = self._verify_dmarc(domain)
                record.verified, record.error = verified, error
                domain.dmarc_verified = verified
                if verified:
                    if policy:
                        domain.dmarc_policy = policy
                    if published_value:
                        domain.dmarc_record = published_value
                        record.value = published_value
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

    def _resolve_optional(self, method: str, host: str) -> list[str]:
        """Call an optional resolver method (resolve_a/resolve_mx/resolve_ns) safely.

        Returns an empty list if the resolver does not implement the method or the
        lookup raises, so a single missing record type never aborts the report."""
        func = getattr(self.resolver, method, None)
        if not callable(func):
            return []
        try:
            return list(func(host))
        except Exception:  # noqa: BLE001 - a failed optional lookup is reported as "absent"
            return []

    def live_verification_report(self, domain_id: int) -> dict[str, object]:
        """Produce a live-DNS verification report in the audited result shape.

        Every field is derived from a real DNS lookup (no placeholder/mock
        states): the domain's A/AAAA, NS, and MX records are resolved live, and
        SPF/DKIM/DMARC are re-verified (persisting the refreshed status). DKIM is
        probed across the configured selector plus common provider selectors and
        stops at the first match. Failing checks include a precise explanation.
        """
        # Re-run the authoritative checks so SPF/DKIM/DMARC reflect live DNS now.
        domain = self.verify_domain(domain_id)
        name = domain.name

        a_records = self._resolve_optional("resolve_a", name)
        ns_records = self._resolve_optional("resolve_ns", name)
        mx_records = self._resolve_optional("resolve_mx", name)

        a_present = bool(a_records)
        ns_present = bool(ns_records)
        mx_present = bool(mx_records)
        # A domain "resolves" if it exists in DNS at all -- delegated zones always
        # publish NS, mail domains publish MX, and most publish A. Any of these is
        # sufficient proof the zone is live.
        dns_resolves = a_present or ns_present or mx_present

        provider = detect_dns_provider(name, self.resolver)

        errors: dict[str, str] = {}
        if not dns_resolves:
            errors["dns_resolves"] = f"No A/AAAA, NS, or MX records found for '{name}'; the domain does not resolve."
        if not mx_present:
            errors["mx_present"] = f"No MX records published for '{name}'; mail cannot be delivered to this domain."
        if not domain.spf_verified:
            errors["spf_valid"] = "No valid SPF (v=spf1) TXT record found at the domain root."
        if not domain.dkim_verified:
            errors["dkim_valid"] = "No valid DKIM record found at the configured or any common provider selector."
        if not domain.dmarc_verified:
            errors["dmarc_valid"] = "No valid DMARC (v=DMARC1) TXT record found at _dmarc."

        timestamp = (domain.last_checked_at or datetime.now(timezone.utc)).isoformat()
        detected = provider.provider if provider.provider != "Unknown" else None

        # Authentication strength derived purely from the live SPF/DKIM/DMARC
        # checks above: "strong" only when all three authenticate, "failed" when
        # none do, "partial" in between. No record is ever assumed without DNS.
        auth_valid = sum((domain.spf_verified, domain.dkim_verified, domain.dmarc_verified))
        if auth_valid == 3:
            verification_strength = "strong"
        elif auth_valid == 0:
            verification_strength = "failed"
        else:
            verification_strength = "partial"

        return {
            "domain": name,
            "dns_resolves": dns_resolves,
            "ns_present": ns_present,
            "a_present": a_present,
            "mx_present": mx_present,
            "spf_valid": domain.spf_verified,
            "dkim_valid": domain.dkim_verified,
            "dkim_selector": domain.metadata.get("dkim_detected_selector"),
            "dmarc_valid": domain.dmarc_verified,
            "dmarc_policy": domain.dmarc_policy if domain.dmarc_verified else None,
            "verification_strength": verification_strength,
            "provider_detected": detected,
            "nameservers": ns_records,
            "mx_hosts": mx_records,
            "verification_timestamp": timestamp,
            "verification_source": "live_dns",
            "errors": errors,
        }


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
            record_reports.append(self._diagnose_record(record, provider, domain))
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

    def _diagnose_record(self, record: DnsRecord, provider: DnsProviderInfo, domain: Domain) -> dict[str, object]:
        # DKIM is detected across many selectors; report against the selector that
        # actually published a record so the diagnosis matches verification.
        if record.record_type is RecordType.DKIM:
            verified, _error, selector = self._verify_dkim(domain)
            host = f"{selector}._domainkey.{domain.name}" if selector else record.host
            try:
                published = self.resolver.resolve_txt(host)
            except Exception:  # noqa: BLE001 - defensive
                published = []
            hint = "" if verified else self._record_hint(record, self._safe_resolve(record.host), provider)
            return {
                "record_type": record.record_type.value,
                "host": host,
                "expected": record.value,
                "published": list(published),
                "verified": verified,
                "error": None if verified else "DKIM record not found at any known selector.",
                "hint": hint,
                "detected_selector": selector,
            }
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

    def _safe_resolve(self, host: str) -> list[str]:
        try:
            return self.resolver.resolve_txt(host)
        except Exception:  # noqa: BLE001 - defensive
            return []

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

    @staticmethod
    def _is_dkim_record(entry: str) -> bool:
        """Return True if a TXT string is a usable (non-revoked) DKIM record.

        Accepts records that either declare ``v=DKIM1`` (the common case) or, for
        the minority of records that omit the optional version tag, carry a
        non-empty ``p=`` public key. A record with an empty ``p=`` is a revoked
        key and is treated as not published."""
        normalized = entry.replace(" ", "")
        lowered = normalized.lower()
        token = DomainService._dkim_public_token(normalized)
        if "v=dkim1" in lowered:
            return bool(token)
        return bool(token)

    def _dkim_selectors(self, domain: Domain) -> list[str]:
        """Ordered, de-duplicated selectors to probe (configured selector first)."""
        ordered: list[str] = []
        seen: set[str] = set()
        for selector in (domain.dkim_selector, *_DKIM_COMMON_SELECTORS):
            normalized = (selector or "").strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    def _verify_dkim(self, domain: Domain) -> tuple[bool, str | None, str | None]:
        """Detect a published DKIM record across common selectors.

        Probes the configured selector first, then the curated provider selector
        list, stopping at the first selector that publishes a usable DKIM record.
        Returns ``(verified, error, matched_selector)``."""
        lookup_failed = False
        for selector in self._dkim_selectors(domain):
            host = f"{selector}._domainkey.{domain.name}"
            try:
                published = self.resolver.resolve_txt(host)
            except Exception:  # noqa: BLE001 - one selector failing must not abort the scan
                lookup_failed = True
                continue
            for entry in published:
                if self._is_dkim_record(entry):
                    return True, None, selector
        if lookup_failed:
            return False, "DKIM record not found (DNS lookup failed for one or more selectors).", None
        return False, "DKIM record not found at any known selector.", None

    def _verify_dmarc(self, domain: Domain) -> tuple[bool, str | None, str | None, str | None]:
        """Verify the DMARC record and read back its policy from DNS.

        Returns ``(verified, error, detected_policy, published_value)`` so the
        caller can store the policy that is actually published instead of one
        chosen manually."""
        host = f"_dmarc.{domain.name}"
        try:
            published = self.resolver.resolve_txt(host)
        except Exception:  # pragma: no cover - defensive
            return False, f"DNS lookup failed for '{host}'.", None, None
        if not published:
            return False, "no TXT record published", None, None
        for entry in published:
            if entry.strip().lower().startswith("v=dmarc1"):
                return True, None, self._detect_dmarc_policy(entry), entry.strip()
        return False, "DMARC record not found", None, None

    @staticmethod
    def _detect_dmarc_policy(value: str) -> str | None:
        """Extract the ``p=`` policy tag from a published DMARC record."""
        match = re.search(r"\bp\s*=\s*([A-Za-z]+)", value)
        if not match:
            return None
        policy = match.group(1).strip().lower()
        return policy if policy in _DMARC_POLICIES else None

    def _require(self, domain_id: int) -> Domain:
        domain = self.repository.get(domain_id)
        if domain is None:
            raise DomainError(f"domain {domain_id} not found")
        return domain
