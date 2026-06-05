from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.api import create_app
from backend.models import ComponentHealth, HealthStatus, LogComponent
from backend.repositories import DomainRepository, LedgerRepository, LogRepository, WarmupRepository
from backend.services import (
    DeliverabilityService,
    DeliveryProvider,
    DeliveryResult,
    DomainService,
    HealthMonitorService,
    LoggingService,
    OutboundMessage,
    WarmupService,
)


class FakeProvider(DeliveryProvider):
    def __init__(self, *, success: bool = True, error: str = "rejected") -> None:
        self.success = success
        self.error = error
        self.messages: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> DeliveryResult:
        self.messages.append(message)
        if self.success:
            return DeliveryResult(True, provider_message_id=f"fake-{len(self.messages)}")
        return DeliveryResult(False, error=self.error)


class FakeResolver:
    def __init__(
        self,
        records: dict[str, list[str]] | None = None,
        *,
        nameservers: list[str] | None = None,
        mx: dict[str, list[str]] | None = None,
        a: dict[str, list[str]] | None = None,
    ) -> None:
        self.records = records or {}
        self.nameservers = nameservers or []
        self.mx = mx or {}
        self.a = a or {}
        self.hosts: list[str] = []

    def resolve_txt(self, host: str) -> list[str]:
        self.hosts.append(host)
        return self.records.get(host, [])

    def resolve_ns(self, host: str) -> list[str]:
        return list(self.nameservers)

    def resolve_mx(self, domain: str) -> list[str]:
        return list(self.mx.get(domain, []))

    def resolve_a(self, host: str) -> list[str]:
        return list(self.a.get(host, []))


class FixedClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime(2025, 1, 1, 9, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: Any) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def ledger_repo() -> LedgerRepository:
    return LedgerRepository(":memory:")


@pytest.fixture
def log_service() -> LoggingService:
    return LoggingService(LogRepository(":memory:"))


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def domain_service(fake_resolver: FakeResolver) -> DomainService:
    return DomainService(DomainRepository(":memory:"), resolver=fake_resolver)


@pytest.fixture
def warmup_service(log_service: LoggingService) -> WarmupService:
    return WarmupService(WarmupRepository(":memory:"), clock=FixedClock(), logger=log_service)


@pytest.fixture
def deliverability_service(ledger_repo: LedgerRepository, domain_service: DomainService, log_service: LoggingService) -> DeliverabilityService:
    return DeliverabilityService(ledger_repo, domain_service, threshold=70, logger=log_service)


@pytest.fixture
def health_service(
    ledger_repo: LedgerRepository,
    domain_service: DomainService,
    warmup_service: WarmupService,
    log_service: LoggingService,
) -> HealthMonitorService:
    def ok_probe(server, *, now):
        return ComponentHealth(server.id, server.kind, HealthStatus.OK, "probe ok", {"host": server.host}, now)

    return HealthMonitorService(
        ledger=ledger_repo,
        domain_service=domain_service,
        warmup_service=warmup_service,
        servers=[{"id": "smtp-1", "host": "smtp.example.com", "kind": "smtp", "port": 587}],
        smtp_probe=ok_probe,
        logger=log_service,
    )


@dataclass(slots=True)
class AppBundle:
    app: Any
    repo: LedgerRepository
    provider: FakeProvider
    domains: DomainService
    warmup: WarmupService
    logger: LoggingService


@pytest.fixture
def app_bundle(
    ledger_repo: LedgerRepository,
    fake_provider: FakeProvider,
    domain_service: DomainService,
    warmup_service: WarmupService,
    log_service: LoggingService,
) -> AppBundle:
    app = create_app(
        repository=ledger_repo,
        provider=fake_provider,
        non_smtp_provider=fake_provider,
        domain_service=domain_service,
        warmup_service=warmup_service,
        logging_service=log_service,
        enforce_verified_domains=True,
    )
    return AppBundle(app, ledger_repo, fake_provider, domain_service, warmup_service, log_service)


@pytest.fixture
def client(app_bundle: AppBundle):
    from fastapi.testclient import TestClient

    return TestClient(app_bundle.app)
