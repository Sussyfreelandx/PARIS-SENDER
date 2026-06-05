"""Delivery service, provider interface, and SMTP provider implementation."""

from __future__ import annotations

import errno
import smtplib
import socket
import ssl
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from backend.models import Campaign, Event, LogComponent, Message, Recipient, Status
from backend.repositories import LedgerRepository
from backend.services.mime import Attachment, build_mime_message


@dataclass(slots=True)
class OutboundMessage:
    """Message passed from DeliveryService to a provider."""

    sender: str
    recipient: str
    subject: str
    content: str
    html: bool = False
    metadata: dict[str, Any] | None = None
    attachments: list[Attachment] | None = None


@dataclass(slots=True)
class DeliveryResult:
    """Provider delivery result.

    ``classification`` and ``stage`` are optional, observability-oriented fields
    used by the direct-to-MX (non-SMTP) path so callers can distinguish a
    retryable temporary failure from a permanent one or a network/port block:

    * ``classification`` is one of :data:`TEMP_FAIL`, :data:`PERM_FAIL`, or
      :data:`BLOCKED` (``None`` on success or when a provider does not classify).
    * ``stage`` names where the failure happened (``"mx"``, ``"connect"``,
      ``"smtp"``, or ``"recipient"``).
    """

    success: bool
    provider_message_id: str | None = None
    error: str | None = None
    classification: str | None = None
    stage: str | None = None


# Failure classifications for delivery results. These let the UI and the retry
# layer reason about *why* a send failed instead of treating every error the same.
TEMP_FAIL = "TEMP_FAIL"  # transient (greylisting, 4xx, timeout): safe to retry later
PERM_FAIL = "PERM_FAIL"  # permanent (invalid domain, no MX, 5xx): retrying will not help
BLOCKED = "BLOCKED"  # network/provider restriction (port 25 blocked, connection refused)


@dataclass(slots=True)
class SendReceipt:
    """DeliveryService result tied to ledger rows."""

    recipient: Recipient
    message: Message
    result: DeliveryResult
    attempts: int = 1


class DeliveryProvider(ABC):
    """Abstract delivery provider seam for SMTP/VPS/MX/other backends."""

    @abstractmethod
    def send(self, message: OutboundMessage) -> DeliveryResult:
        """Send one message and return a result."""


class SMTPClient(Protocol):
    """Protocol implemented by smtplib clients and test fakes."""

    def send_message(self, msg: Any) -> Any: ...
    def quit(self) -> Any: ...


@dataclass(slots=True)
class SMTPConfig:
    """SMTP connection settings for SMTPDeliveryProvider."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    use_ssl: bool = False
    timeout: float = 30.0
    allow_insecure_ssl: bool = False


SMTPFactory = Callable[[SMTPConfig, ssl.SSLContext], SMTPClient]


# OS-level error numbers that indicate the network path to the MX host is
# blocked/refused (port 25 firewalled, host unreachable, connection reset) rather
# than a transient server condition. Used to classify direct-MX failures as BLOCKED.
_BLOCKED_ERRNOS: frozenset[int] = frozenset(
    code
    for code in (
        getattr(errno, "ECONNREFUSED", None),
        getattr(errno, "ETIMEDOUT", None),
        getattr(errno, "ENETUNREACH", None),
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ECONNRESET", None),
        getattr(errno, "ENETDOWN", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "ECONNABORTED", None),
    )
    if code is not None
)


class NonSmtpDeliveryProvider(DeliveryProvider):
    """Non-SMTP provider seam using the shared MIME builder and injectable transport."""

    def __init__(self, sender: Callable[[OutboundMessage], DeliveryResult] | None = None) -> None:
        self.sender = sender or self._default_sender

    def send(self, message: OutboundMessage) -> DeliveryResult:
        """Build a MIME message and hand the enriched outbound message to the transport."""
        mime_message = build_mime_message(
            message.sender,
            message.recipient,
            message.subject,
            message.content,
            html=message.html,
            attachments=message.attachments,
        )
        metadata = dict(message.metadata or {})
        metadata["mime_message"] = mime_message
        outbound = OutboundMessage(
            sender=message.sender,
            recipient=message.recipient,
            subject=message.subject,
            content=message.content,
            html=message.html,
            metadata=metadata,
            attachments=message.attachments,
        )
        return self.sender(outbound)

    def _default_sender(self, message: OutboundMessage) -> DeliveryResult:
        raise RuntimeError("non-SMTP transport is not configured")


class SMTPDeliveryProvider(DeliveryProvider):
    """SMTP provider using stdlib smtplib with injectable transport."""

    def __init__(self, config: SMTPConfig, smtp_factory: SMTPFactory | None = None) -> None:
        self.config = config
        self.smtp_factory = smtp_factory or self._default_smtp_factory

    def send(self, message: OutboundMessage) -> DeliveryResult:
        """Build and send a MIME message via SMTP."""
        mime_message = build_mime_message(
            message.sender,
            message.recipient,
            message.subject,
            message.content,
            html=message.html,
            attachments=message.attachments,
        )
        client: SMTPClient | None = None
        try:
            context = self._build_ssl_context()
            client = self.smtp_factory(self.config, context)
            client.send_message(mime_message)
            provider_id = mime_message.get("Message-ID") or f"smtp:{message.recipient}"
            return DeliveryResult(success=True, provider_message_id=provider_id)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    pass

    def verify_connection(self) -> DeliveryResult:
        """Open an SMTP connection (and authenticate if credentials are set) to
        validate the configuration, then close it without sending mail.

        Returns a successful result when the relay accepts the connection/login,
        otherwise a failed result carrying the error message. The connection is
        always closed, so this never leaves a socket open."""
        client: SMTPClient | None = None
        try:
            context = self._build_ssl_context()
            client = self.smtp_factory(self.config, context)
            noop = getattr(client, "noop", None)
            if callable(noop):
                try:
                    noop()
                except Exception:  # noqa: BLE001 - NOOP support is optional; connect+login already validated
                    pass
            return DeliveryResult(success=True, provider_message_id=f"smtp:{self.config.host}:{self.config.port}")
        except Exception as exc:  # noqa: BLE001 - surface the connection/login error to the UI
            return DeliveryResult(success=False, error=str(exc))
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    pass

    def _default_smtp_factory(self, config: SMTPConfig, context: ssl.SSLContext) -> SMTPClient:
        if config.use_ssl:
            client: Any = smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout, context=context)
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
            if config.use_tls:
                client.starttls(context=context)
        if config.username and config.password:
            client.login(config.username, config.password)
        return client

    def _build_ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if self.config.allow_insecure_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context


@dataclass(slots=True)
class DirectMxConfig:
    """Configuration for direct-to-MX (non-SMTP-account) delivery."""

    helo_hostname: str | None = None
    port: int = 25
    timeout: float = 30.0
    use_starttls: bool = True
    allow_insecure_ssl: bool = True


class MxResolver(Protocol):
    """Resolver seam returning the mail exchangers for a domain, best first."""

    def resolve_mx(self, domain: str) -> list[str]: ...


class DnspythonMxResolver:
    """Default MX resolver backed by dnspython, sorted by preference."""

    def resolve_mx(self, domain: str) -> list[str]:
        import dns.resolver  # imported lazily so the dependency is optional in tests

        try:
            answers = dns.resolver.resolve(domain, "MX")
        except Exception:
            return []
        hosts = sorted(
            ((int(getattr(rdata, "preference", 0)), str(getattr(rdata, "exchange", rdata)).rstrip(".")) for rdata in answers),
            key=lambda item: item[0],
        )
        return [host for _, host in hosts if host]


class DirectMxDeliveryProvider(DeliveryProvider):
    """Non-SMTP provider that delivers directly to each recipient's MX servers.

    This is the "non-SMTP" send path: instead of relaying through a configured
    SMTP account, it resolves the recipient domain's MX records and connects to
    them on port 25 to hand off the message, acting as its own MTA. Both the MX
    resolver and the SMTP transport are injectable so the path is fully testable
    without real network access.
    """

    def __init__(
        self,
        config: DirectMxConfig | None = None,
        *,
        mx_resolver: MxResolver | None = None,
        smtp_factory: Callable[[str, DirectMxConfig, ssl.SSLContext], SMTPClient] | None = None,
    ) -> None:
        self.config = config or DirectMxConfig()
        self.mx_resolver = mx_resolver or DnspythonMxResolver()
        self.smtp_factory = smtp_factory or self._default_smtp_factory

    def send(self, message: OutboundMessage) -> DeliveryResult:
        """Resolve the recipient's MX hosts and deliver to the first that accepts.

        Failures are never collapsed into a generic "failed": a missing MX record
        yields a permanent ``no_mx_records_found`` error, a blocked/refused port 25
        yields ``connection_blocked_or_rejected`` (BLOCKED), and the real per-host
        reason is preserved across the whole MX fallback chain. Every returned
        result carries a ``classification`` (TEMP_FAIL/PERM_FAIL/BLOCKED) so the
        retry layer and UI can act on it.
        """
        domain = message.recipient.split("@", 1)[1].strip().lower() if "@" in message.recipient else ""
        if not domain:
            return DeliveryResult(
                success=False,
                error=f"invalid_recipient: invalid recipient address: {message.recipient!r}",
                classification=PERM_FAIL,
                stage="recipient",
            )
        hosts = self.mx_resolver.resolve_mx(domain)
        if not hosts:
            return DeliveryResult(
                success=False,
                error=f"no_mx_records_found: no MX records found for domain {domain!r}",
                classification=PERM_FAIL,
                stage="mx",
            )
        mime_message = build_mime_message(
            message.sender,
            message.recipient,
            message.subject,
            message.content,
            html=message.html,
            attachments=message.attachments,
        )
        context = self._build_ssl_context()
        # Try each MX host in preference order; keep the real, classified reason
        # for every host so an exhausted chain still explains exactly what failed.
        failures: list[DeliveryResult] = []
        for host in hosts:
            result = self._deliver_to_host(host, mime_message, message, context)
            if result.success:
                return result
            failures.append(result)
        return self._aggregate_failures(domain, failures)

    def _deliver_to_host(
        self,
        host: str,
        mime_message: Any,
        message: OutboundMessage,
        context: ssl.SSLContext,
    ) -> DeliveryResult:
        """Attempt delivery to a single MX host, returning a classified result."""
        client: SMTPClient | None = None
        try:
            client = self.smtp_factory(host, self.config, context)
            client.send_message(mime_message)
            provider_id = mime_message.get("Message-ID") or f"mx:{host}:{message.recipient}"
            return DeliveryResult(success=True, provider_message_id=provider_id)
        except Exception as exc:  # try the next MX host on failure
            classification, stage, code = self._classify_exception(exc)
            return DeliveryResult(
                success=False,
                error=f"{code}: MX host {host!r}: {exc}",
                classification=classification,
                stage=stage,
            )
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    pass

    def _aggregate_failures(self, domain: str, failures: list[DeliveryResult]) -> DeliveryResult:
        """Combine per-host failures into one classified result for the chain.

        Classification precedence is retry-biased so the chain is only declared
        permanently failed when *every* host gave a permanent answer: if all
        hosts were BLOCKED we surface the explicit block, otherwise any BLOCKED
        or TEMP_FAIL host makes the whole attempt retryable (TEMP_FAIL), and only
        an all-permanent chain is reported as PERM_FAIL.
        """
        details = "; ".join(f.error for f in failures if f.error)
        classifications = {f.classification for f in failures}
        if failures and classifications == {BLOCKED}:
            classification = BLOCKED
            code = "connection_blocked_or_rejected"
        elif BLOCKED in classifications or TEMP_FAIL in classifications:
            classification = TEMP_FAIL
            code = "temporary_delivery_failure"
        else:
            classification = PERM_FAIL
            code = "permanent_delivery_failure"
        stage = failures[-1].stage if failures else "mx"
        return DeliveryResult(
            success=False,
            error=f"{code}: delivery to all MX hosts failed for {domain!r} [{details}]",
            classification=classification,
            stage=stage,
        )

    @staticmethod
    def _classify_exception(exc: Exception) -> tuple[str, str, str]:
        """Map a transport exception to ``(classification, stage, error_code)``.

        Distinguishes a blocked/refused network path (the classic "port 25 is
        blocked by the host/ISP" case) from a temporary server condition and a
        permanent SMTP rejection, so retrying only happens when it can help.
        """
        # SMTP-level responses carry an authoritative numeric code: 4xx is
        # transient, 5xx is permanent.
        if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
            return PERM_FAIL, "smtp", "recipient_or_sender_refused"
        if isinstance(exc, smtplib.SMTPResponseException):
            code = getattr(exc, "smtp_code", 0) or 0
            if 400 <= code < 500:
                return TEMP_FAIL, "smtp", "temporary_smtp_error"
            if code >= 500:
                return PERM_FAIL, "smtp", "permanent_smtp_error"
            return TEMP_FAIL, "smtp", "smtp_error"
        if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
            return TEMP_FAIL, "connect", "smtp_connection_failed"
        if isinstance(exc, ConnectionRefusedError):
            return BLOCKED, "connect", "connection_blocked_or_rejected"
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return BLOCKED, "connect", "connection_blocked_or_rejected"
        if isinstance(exc, OSError):
            if exc.errno in _BLOCKED_ERRNOS:
                return BLOCKED, "connect", "connection_blocked_or_rejected"
            return TEMP_FAIL, "connect", "network_error"
        return TEMP_FAIL, "smtp", "delivery_error"

    def _default_smtp_factory(self, host: str, config: DirectMxConfig, context: ssl.SSLContext) -> SMTPClient:
        client: Any = smtplib.SMTP(host, config.port, timeout=config.timeout)
        if config.use_starttls:
            try:
                client.starttls(context=context)
            except smtplib.SMTPNotSupportedError:
                # Many MX hosts accept plaintext on port 25; STARTTLS is best-effort.
                pass
        return client

    def _build_ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if self.config.allow_insecure_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context


class DeliveryService:
    """Orchestrates ledger writes and delegates network delivery to providers."""

    def __init__(
        self,
        ledger: LedgerRepository,
        provider: DeliveryProvider,
        logger: Any | None = None,
        *,
        max_attempts: int = 1,
        backoff_base: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_cap: float = 30.0,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.logger = logger
        # Retry policy. ``max_attempts == 1`` preserves the original single-shot
        # behaviour; values > 1 enable exponential backoff between attempts.
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = max(0.0, float(backoff_base))
        self.backoff_factor = max(1.0, float(backoff_factor))
        self.backoff_cap = max(0.0, float(backoff_cap))
        self._sleep = sleeper or time.sleep

    def _backoff_delay(self, attempt: int) -> float:
        """Return the delay (seconds) to wait before retry ``attempt`` (1-indexed retry)."""
        if self.backoff_base <= 0:
            return 0.0
        delay = self.backoff_base * (self.backoff_factor ** (attempt - 1))
        return min(delay, self.backoff_cap) if self.backoff_cap else delay

    def _attempt_send(self, outbound: "OutboundMessage") -> DeliveryResult:
        """Call the provider once, converting an unexpected exception into a
        failed :class:`DeliveryResult` carrying the error so the reason is never
        silently swallowed. Kept as a small, reusable seam for callers that want
        a single attempt without the retry loop."""
        try:
            return self.provider.send(outbound)
        except Exception as exc:  # noqa: BLE001 - surface provider crash as a real failure reason
            return DeliveryResult(success=False, error=f"{type(exc).__name__}: {exc}")

    def _deliver_with_retries(
        self,
        message: Message,
        outbound: "OutboundMessage",
        *,
        campaign_id: int,
        delivery_channel: str | None,
    ) -> tuple[DeliveryResult, int]:
        """Attempt delivery up to ``max_attempts`` times with exponential backoff.

        Every attempt is recorded; failures are logged with structured,
        actionable diagnostics (campaign/message id, recipient, provider
        response, retry count, and a stack trace when one is available). Returns
        the final result and the number of attempts made.
        """
        result = DeliveryResult(success=False, error="delivery not attempted")
        attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            try:
                result = self.provider.send(outbound)
            except Exception as exc:  # noqa: BLE001 - never hide a provider crash
                result = DeliveryResult(
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._log_failure(
                    message,
                    outbound.recipient,
                    result,
                    attempt=attempt,
                    campaign_id=campaign_id,
                    delivery_channel=delivery_channel,
                    stack=traceback.format_exc(),
                )
            else:
                if result.success:
                    return result, attempts
                self._log_failure(
                    message,
                    outbound.recipient,
                    result,
                    attempt=attempt,
                    campaign_id=campaign_id,
                    delivery_channel=delivery_channel,
                    stack=None,
                )
            if attempt < self.max_attempts:
                delay = self._backoff_delay(attempt)
                if delay > 0:
                    self._sleep(delay)
        return result, attempts

    def _log_failure(
        self,
        message: Message,
        recipient: str,
        result: DeliveryResult,
        *,
        attempt: int,
        campaign_id: int,
        delivery_channel: str | None,
        stack: str | None,
    ) -> None:
        if self.logger is None:
            return
        final = attempt >= self.max_attempts
        context: dict[str, Any] = {
            "campaign_id": campaign_id,
            "message_id": message.id,
            "recipient": recipient,
            "provider_response": result.error,
            "provider_message_id": result.provider_message_id,
            "attempt": attempt,
            "max_attempts": self.max_attempts,
            "final": final,
        }
        if delivery_channel is not None:
            context["delivery_channel"] = delivery_channel
        if stack:
            context["stack_trace"] = stack
        self.logger.log(
            LogComponent.DELIVERY,
            "ERROR" if final else "WARNING",
            "message delivery attempt failed" if not final else "message delivery failed after retries",
            **context,
        )

    def send_campaign(
        self,
        campaign: Campaign,
        recipients: Iterable[str],
        subject: str,
        content: str,
        *,
        sender: str,
        html: bool = False,
        delivery_channel: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> list[SendReceipt]:
        """Send a campaign to recipients while persisting every status event."""
        persisted_campaign = campaign if campaign.id is not None else self.ledger.create_campaign(campaign)
        if persisted_campaign.id is None:
            raise ValueError("campaign must have an id after persistence")

        attachment_list = list(attachments) if attachments else None
        receipts: list[SendReceipt] = []
        recipient_list = list(recipients)
        for email in recipient_list:
            recipient = self.ledger.add_recipient(persisted_campaign.id, email)
            if recipient.id is None:
                raise ValueError("recipient must have an id after persistence")
            message = self.ledger.create_message(
                Message(
                    campaign_id=persisted_campaign.id,
                    recipient_id=recipient.id,
                    subject=subject,
                    content=content,
                    status=Status.QUEUED,
                )
            )
            self._record(message, Status.QUEUED)
            self._record(message, Status.PROCESSING)
            result, attempts = self._deliver_with_retries(
                message,
                OutboundMessage(
                    sender=sender,
                    recipient=email,
                    subject=subject,
                    content=content,
                    html=html,
                    attachments=attachment_list,
                ),
                campaign_id=persisted_campaign.id,
                delivery_channel=delivery_channel,
            )
            if result.success:
                self._record(message, Status.SENT, provider_message_id=result.provider_message_id)
            else:
                # Real, provider-sourced failure reason is persisted to the ledger
                # (events table) so it is queryable end-to-end. Messages that stay
                # FAILED form the inspectable dead-letter set.
                self._record(message, Status.FAILED, error=result.error)
            updated_message = self.ledger.get_message(message.id or 0) or message
            receipts.append(
                SendReceipt(recipient=recipient, message=updated_message, result=result, attempts=attempts)
            )
        if self.logger is not None:
            sent = sum(1 for receipt in receipts if receipt.result.success)
            failed = len(receipts) - sent
            severity = "ERROR" if failed else "INFO"
            context = {
                "campaign_id": persisted_campaign.id,
                "requested": len(recipient_list),
                "sent": sent,
                "failed": failed,
                "sender": sender,
                "html": html,
            }
            if delivery_channel is not None:
                context["delivery_channel"] = delivery_channel
            self.logger.log(
                LogComponent.DELIVERY,
                severity,
                "campaign delivery completed",
                **context,
            )
        return receipts

    def _record(
        self,
        message: Message,
        status: Status,
        *,
        provider_message_id: str | None = None,
        error: str | None = None,
    ) -> Event:
        if message.id is None:
            raise ValueError("message must be persisted before recording events")
        return self.ledger.record_event(
            Event(message_id=message.id, status=status, provider_message_id=provider_message_id, error=error)
        )
