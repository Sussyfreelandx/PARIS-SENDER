"""Service exports."""

from backend.services.deliverability import DeliverabilityService
from backend.services.delivery import (
    DeliveryProvider,
    DeliveryResult,
    DeliveryService,
    DirectMxConfig,
    DirectMxDeliveryProvider,
    NonSmtpDeliveryProvider,
    OutboundMessage,
    SMTPConfig,
    SMTPDeliveryProvider,
)
from backend.services.domain import (
    DnsProviderInfo,
    DomainError,
    DomainService,
    build_dkim_record,
    build_dmarc_record,
    build_spf_record,
    detect_dns_provider,
    generate_dkim_keypair,
    is_valid_domain,
)
from backend.services.health import HealthMonitorService, ServerProbe, SmtplibProbe, start_health_monitor, stop_health_monitor
from backend.services.logging_service import LoggingService, start_log_archiver, stop_log_archiver
from backend.services.mime import Attachment, build_mime_message
from backend.services.security import SecurityService
from backend.services.warmup import WarmupDecision, WarmupService, start_warmup_scheduler, stop_warmup_scheduler

__all__ = [
    "Attachment",
    "DeliverabilityService",
    "DeliveryProvider",
    "DeliveryResult",
    "DeliveryService",
    "DirectMxConfig",
    "DirectMxDeliveryProvider",
    "DnsProviderInfo",
    "DomainError",
    "DomainService",
    "HealthMonitorService",
    "LoggingService",
    "NonSmtpDeliveryProvider",
    "OutboundMessage",
    "SMTPConfig",
    "SMTPDeliveryProvider",
    "SecurityService",
    "ServerProbe",
    "SmtplibProbe",
    "WarmupDecision",
    "WarmupService",
    "build_dkim_record",
    "build_dmarc_record",
    "build_mime_message",
    "build_spf_record",
    "detect_dns_provider",
    "generate_dkim_keypair",
    "is_valid_domain",
    "start_health_monitor",
    "start_log_archiver",
    "start_warmup_scheduler",
    "stop_health_monitor",
    "stop_log_archiver",
    "stop_warmup_scheduler",
]
