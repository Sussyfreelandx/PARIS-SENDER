"""Delivery service, provider interface, and SMTP provider implementation."""

from __future__ import annotations

import smtplib
import ssl
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
    """Provider delivery result."""

    success: bool
    provider_message_id: str | None = None
    error: str | None = None


@dataclass(slots=True)
class SendReceipt:
    """DeliveryService result tied to ledger rows."""

    recipient: Recipient
    message: Message
    result: DeliveryResult


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
        """Resolve the recipient's MX hosts and deliver to the first that accepts."""
        domain = message.recipient.split("@", 1)[1].strip().lower() if "@" in message.recipient else ""
        if not domain:
            return DeliveryResult(success=False, error=f"invalid recipient address: {message.recipient!r}")
        hosts = self.mx_resolver.resolve_mx(domain)
        if not hosts:
            return DeliveryResult(success=False, error=f"no MX records found for domain {domain!r}")
        mime_message = build_mime_message(
            message.sender,
            message.recipient,
            message.subject,
            message.content,
            html=message.html,
            attachments=message.attachments,
        )
        context = self._build_ssl_context()
        last_error: str | None = None
        for host in hosts:
            client: SMTPClient | None = None
            try:
                client = self.smtp_factory(host, self.config, context)
                client.send_message(mime_message)
                provider_id = mime_message.get("Message-ID") or f"mx:{host}:{message.recipient}"
                return DeliveryResult(success=True, provider_message_id=provider_id)
            except Exception as exc:  # try the next MX host on failure
                last_error = str(exc)
            finally:
                if client is not None:
                    try:
                        client.quit()
                    except Exception:
                        pass
        return DeliveryResult(success=False, error=last_error or f"delivery to MX hosts failed for {domain!r}")

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

    def __init__(self, ledger: LedgerRepository, provider: DeliveryProvider, logger: Any | None = None) -> None:
        self.ledger = ledger
        self.provider = provider
        self.logger = logger

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
            try:
                result = self.provider.send(
                    OutboundMessage(
                        sender=sender,
                        recipient=email,
                        subject=subject,
                        content=content,
                        html=html,
                        attachments=attachment_list,
                    )
                )
                if result.success:
                    self._record(message, Status.SENT, provider_message_id=result.provider_message_id)
                else:
                    self._record(message, Status.FAILED, error=result.error)
            except Exception as exc:
                result = DeliveryResult(success=False, error=str(exc))
                self._record(message, Status.FAILED, error=str(exc))
            updated_message = self.ledger.get_message(message.id or 0) or message
            receipts.append(SendReceipt(recipient=recipient, message=updated_message, result=result))
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
