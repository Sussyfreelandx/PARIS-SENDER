"""FastAPI application exposing the backend service seam."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.models import HealthServer, LogComponent, LogSeverity, Status, WarmupConfig
from backend.repositories import DomainRepository, LedgerRepository, LogRepository, WarmupRepository
from backend.services import (
    DeliverabilityService,
    DeliveryProvider,
    DeliveryResult,
    DeliveryService,
    DomainError,
    DomainService,
    HealthMonitorService,
    LoggingService,
    OutboundMessage,
    WarmupService,
    start_health_monitor,
    start_log_archiver,
    stop_health_monitor,
    stop_log_archiver,
)
from backend.validators import AutograbService
from backend.validators.compose import analyze_compose

from backend.api.security import AuthMiddleware, RateLimitMiddleware


class CampaignCreate(BaseModel):
    """Request to create a campaign."""

    name: str = Field(..., min_length=1)


class SendRequest(BaseModel):
    """Request to send a campaign."""

    recipients: list[str] = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    sender: str = Field("sender@example.com", min_length=1)
    html: bool = False
    non_smtp_delivery: bool = False


class PredictRequest(BaseModel):
    """Request to predict deliverability before sending."""

    recipients: list[str] = Field(default_factory=list)
    subject: str = Field("", min_length=0)
    content: str = Field(..., min_length=1)
    sender: str = Field("sender@example.com", min_length=1)
    html: bool = False


class WarmupDomainRequest(BaseModel):
    """Request to enable or update warmup for a domain."""

    domain: str = Field(..., min_length=1)
    daily_limit: int = Field(100, ge=1)
    max_per_batch: int = Field(25, ge=1)
    max_per_hour: int = Field(20, ge=1)
    ramp_start_limit: int = Field(10, ge=1)
    ramp_days: int = Field(7, ge=1)
    enabled: bool = True


class WarmupOverrideRequest(BaseModel):
    """Local admin override request; authorized must be true to apply changes."""

    authorized: bool = False
    daily_limit: int | None = Field(None, ge=1)
    max_per_batch: int | None = Field(None, ge=1)
    max_per_hour: int | None = Field(None, ge=1)
    bypass_remaining: bool = False
    detail: str | None = None


class DomainCreate(BaseModel):
    """Request to onboard a sending domain."""

    name: str = Field(..., min_length=1)
    selector: str = Field("paris", min_length=1)
    dmarc_policy: str = Field("none")
    spf_includes: list[str] = Field(default_factory=list)


class DmarcPolicyUpdate(BaseModel):
    """Request to change a domain's DMARC policy."""

    policy: str = Field(..., min_length=1)


class PreviewRequest(BaseModel):
    """Request to render a template preview with autograb personalization."""

    template: str = Field(..., min_length=1)
    email: str = Field("recipient@example.com", min_length=1)
    html: bool = False


class AnalyzeRequest(BaseModel):
    """Request to analyze compose content for spam/ratio/placeholder issues."""

    content: str = Field(..., min_length=1)
    html: bool = False


class ProviderNotConfigured(DeliveryProvider):
    """Default provider that makes missing dependency wiring explicit."""

    def __init__(self, message: str = "delivery provider is not configured") -> None:
        self.message = message

    def send(self, message: OutboundMessage) -> DeliveryResult:
        raise RuntimeError(self.message)


def _sender_domain(sender: str) -> str | None:
    if "@" not in sender:
        return None
    return sender.split("@", 1)[1].strip().lower() or None


def create_app(
    *,
    repository: LedgerRepository | None = None,
    provider: DeliveryProvider | None = None,
    non_smtp_provider: DeliveryProvider | None = None,
    domain_repository: DomainRepository | None = None,
    domain_service: DomainService | None = None,
    repository_factory: Callable[[], LedgerRepository] | None = None,
    provider_factory: Callable[[], DeliveryProvider] | None = None,
    non_smtp_provider_factory: Callable[[], DeliveryProvider] | None = None,
    deliverability_service: DeliverabilityService | None = None,
    warmup_service: WarmupService | None = None,
    health_service: HealthMonitorService | None = None,
    logging_service: LoggingService | None = None,
    health_servers: list[HealthServer | dict[str, Any]] | None = None,
    enforce_verified_domains: bool = True,
    min_deliverability_score: int = 70,
    enable_warmup_scheduler: bool = False,
    enable_health_monitor: bool = False,
    enable_log_archiver: bool = False,
    enable_auth: bool = False,
    enable_rate_limit: bool = False,
    jwt_secret: str | None = None,
    rate_limit_requests: int = 60,
    rate_limit_window_seconds: int = 60,
) -> FastAPI:
    """Create a FastAPI app with injectable services and opt-in security controls."""
    app = FastAPI(title="Paris Sender Backend")
    if enable_rate_limit:
        app.add_middleware(
            RateLimitMiddleware,
            requests=rate_limit_requests,
            window_seconds=rate_limit_window_seconds,
        )
    if enable_auth:
        app.add_middleware(AuthMiddleware, secret=jwt_secret)
    repo_singleton = repository or (repository_factory() if repository_factory else LedgerRepository(":memory:"))
    provider_singleton = provider or (provider_factory() if provider_factory else ProviderNotConfigured())
    non_smtp_provider_singleton = non_smtp_provider or (
        non_smtp_provider_factory()
        if non_smtp_provider_factory
        else ProviderNotConfigured("non-SMTP delivery provider is not configured")
    )
    domain_service_singleton = domain_service or DomainService(domain_repository or DomainRepository(":memory:"))
    logging_singleton = logging_service or LoggingService(LogRepository(":memory:"))
    deliverability_singleton = deliverability_service or DeliverabilityService(
        repo_singleton, domain_service_singleton, threshold=min_deliverability_score, logger=logging_singleton
    )
    if getattr(deliverability_singleton, "logger", None) is None:
        deliverability_singleton.logger = logging_singleton
    warmup_singleton = warmup_service or WarmupService(WarmupRepository(":memory:"), logger=logging_singleton)
    if getattr(warmup_singleton, "logger", None) is None:
        warmup_singleton.logger = logging_singleton
    health_singleton = health_service or HealthMonitorService(
        ledger=repo_singleton,
        domain_service=domain_service_singleton,
        warmup_service=warmup_singleton,
        servers=health_servers,
        logger=logging_singleton,
    )
    if getattr(health_singleton, "logger", None) is None:
        health_singleton.logger = logging_singleton
    scheduler_task: asyncio.Task[Any] | None = None
    health_monitor_task: asyncio.Task[Any] | None = None
    log_archiver_task: asyncio.Task[Any] | None = None
    autograb = AutograbService()

    def get_repository() -> LedgerRepository:
        return repo_singleton

    def get_provider() -> DeliveryProvider:
        return provider_singleton

    def get_non_smtp_provider() -> DeliveryProvider:
        return non_smtp_provider_singleton

    def get_domain_service() -> DomainService:
        return domain_service_singleton

    def get_deliverability_service() -> DeliverabilityService:
        return deliverability_singleton

    def get_warmup_service() -> WarmupService:
        return warmup_singleton

    def get_health_service() -> HealthMonitorService:
        return health_singleton

    def get_logging_service() -> LoggingService:
        return logging_singleton

    if enable_warmup_scheduler:
        @app.on_event("startup")
        async def _start_warmup_scheduler() -> None:
            nonlocal scheduler_task
            scheduler_task = asyncio.create_task(warmup_singleton.run_ramp_scheduler())

        @app.on_event("shutdown")
        async def _stop_warmup_scheduler() -> None:
            if scheduler_task is not None:
                scheduler_task.cancel()

    if enable_health_monitor:
        @app.on_event("startup")
        async def _start_health_monitor() -> None:
            nonlocal health_monitor_task
            health_monitor_task = start_health_monitor(health_singleton)

        @app.on_event("shutdown")
        async def _stop_health_monitor() -> None:
            await stop_health_monitor(health_monitor_task)

    if enable_log_archiver:
        @app.on_event("startup")
        async def _start_log_archiver() -> None:
            nonlocal log_archiver_task
            log_archiver_task = start_log_archiver(logging_singleton)

        @app.on_event("shutdown")
        async def _stop_log_archiver() -> None:
            await stop_log_archiver(log_archiver_task)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        # Friendly landing page so opening the backend root in a browser (or an
        # Electron probe) shows a clear "service is running" status instead of a
        # bare 404, which previously made the standalone executable look broken.
        return (
            "<!doctype html><html lang=\"en\"><head>"
            "<meta charset=\"utf-8\"><title>Paris Sender Backend</title>"
            "<style>body{font-family:system-ui,sans-serif;background:#0f172a;"
            "color:#e2e8f0;display:flex;min-height:100vh;align-items:center;"
            "justify-content:center;margin:0}main{text-align:center}"
            "code{background:#1e293b;padding:2px 6px;border-radius:4px}"
            "a{color:#38bdf8}</style></head><body><main>"
            "<h1>Paris Sender backend is running</h1>"
            "<p>This is the local API service. The desktop app talks to it "
            "automatically.</p>"
            "<p>Health check: <a href=\"/health\"><code>/health</code></a> &middot; "
            "API docs: <a href=\"/docs\"><code>/docs</code></a></p>"
            "</main></body></html>"
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        # Browsers automatically request /favicon.ico; return 204 so it does not
        # surface as a noisy 404 in the server console.
        return Response(status_code=204)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/status")
    def health_status(health_monitor: HealthMonitorService = Depends(get_health_service)) -> dict[str, Any]:
        return health_monitor.snapshot()

    @app.get("/health/domain/{domain}")
    def health_domain(domain: str, health_monitor: HealthMonitorService = Depends(get_health_service)) -> dict[str, Any]:
        try:
            return health_monitor.domain_health(domain)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/health/server/{server_id}")
    def health_server(server_id: str, health_monitor: HealthMonitorService = Depends(get_health_service)) -> dict[str, Any]:
        try:
            return health_monitor.server_health(server_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/logs")
    def get_logs(
        severity: str | None = None,
        component: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        logger: LoggingService = Depends(get_logging_service),
    ) -> dict[str, Any]:
        try:
            return {"logs": logger.query(severity=severity, component=component, since=since, until=until, limit=limit)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/logs/summary")
    def get_log_summary(logger: LoggingService = Depends(get_logging_service)) -> dict[str, Any]:
        return logger.summary()

    @app.post("/campaigns", status_code=201)
    def create_campaign(payload: CampaignCreate, repo: LedgerRepository = Depends(get_repository)) -> dict[str, Any]:
        campaign = repo.create_campaign(payload.name)
        return {"id": campaign.id, "name": campaign.name, "created_at": campaign.created_at.isoformat()}

    @app.post("/campaigns/{campaign_id}/send")
    def send_campaign(
        campaign_id: int,
        payload: SendRequest,
        repo: LedgerRepository = Depends(get_repository),
        delivery_provider: DeliveryProvider = Depends(get_provider),
        non_smtp_delivery_provider: DeliveryProvider = Depends(get_non_smtp_provider),
        domains: DomainService = Depends(get_domain_service),
        deliverability: DeliverabilityService = Depends(get_deliverability_service),
        warmup: WarmupService = Depends(get_warmup_service),
        logger: LoggingService = Depends(get_logging_service),
    ) -> dict[str, Any]:
        campaign = repo.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        delivery_channel = "non_smtp" if payload.non_smtp_delivery else "smtp"
        logger.info(
            LogComponent.CAMPAIGN,
            "campaign send requested",
            campaign_id=campaign_id,
            recipients=len(payload.recipients),
            sender=payload.sender,
            html=payload.html,
            non_smtp_delivery=payload.non_smtp_delivery,
            delivery_channel=delivery_channel,
        )
        _enforce_domain(domains, payload.sender, enforce_verified_domains)
        score = deliverability.predict(
            _score_content(payload.subject, payload.content),
            payload.recipients,
            sender=payload.sender,
            html=payload.html,
        )
        logger.info(
            LogComponent.CAMPAIGN,
            "deliverability gate evaluated",
            campaign_id=campaign_id,
            score=score.score,
            threshold=score.threshold,
            passed=score.passed,
            delivery_channel=delivery_channel,
        )
        if not score.passed:
            logger.warning(
                LogComponent.CAMPAIGN,
                "campaign send blocked by deliverability gate",
                campaign_id=campaign_id,
                score=score.score,
                threshold=score.threshold,
                non_smtp_delivery=payload.non_smtp_delivery,
                delivery_channel=delivery_channel,
            )
            raise HTTPException(
                status_code=400,
                detail=f"deliverability score {score.score} is below required threshold {score.threshold}",
            )
        sender_domain = _sender_domain(payload.sender)
        if sender_domain and warmup.is_warmup(sender_domain):
            decision = warmup.check_send(sender_domain, len(payload.recipients))
            logger.info(
                LogComponent.CAMPAIGN,
                "warmup gate evaluated",
                campaign_id=campaign_id,
                domain=sender_domain,
                decision=decision.to_dict(),
                non_smtp_delivery=payload.non_smtp_delivery,
                delivery_channel=delivery_channel,
            )
            if decision.blocked:
                logger.warning(
                    LogComponent.CAMPAIGN,
                    "campaign send blocked by warmup gate",
                    campaign_id=campaign_id,
                    domain=sender_domain,
                    reason=decision.reason,
                    allowed_count=decision.allowed_count,
                    non_smtp_delivery=payload.non_smtp_delivery,
                    delivery_channel=delivery_channel,
                )
                next_at = decision.next_batch_at.isoformat() if decision.next_batch_at else "unknown"
                raise HTTPException(
                    status_code=400,
                    detail=f"warmup limit blocked send: {decision.reason}; allowed now {decision.allowed_count}; next batch at {next_at}",
                )
            warmup.schedule(sender_domain, campaign_id, len(payload.recipients))
        selected_provider = non_smtp_delivery_provider if payload.non_smtp_delivery else delivery_provider
        service = DeliveryService(repo, selected_provider, logger=logger)
        receipts = service.send_campaign(
            campaign,
            payload.recipients,
            payload.subject,
            payload.content,
            sender=payload.sender,
            html=payload.html,
            delivery_channel=delivery_channel,
        )
        if sender_domain and warmup.is_warmup(sender_domain):
            warmup.record_execution(sender_domain, campaign_id, len(receipts))
        sent = sum(1 for receipt in receipts if receipt.result.success)
        failed = sum(1 for receipt in receipts if not receipt.result.success)
        logger.log(
            LogComponent.CAMPAIGN,
            LogSeverity.ERROR if failed else LogSeverity.INFO,
            "campaign send completed",
            campaign_id=campaign_id,
            sent=sent,
            failed=failed,
            messages=[receipt.message.id for receipt in receipts],
            non_smtp_delivery=payload.non_smtp_delivery,
            delivery_channel=delivery_channel,
        )
        return {
            "campaign_id": campaign_id,
            "sent": sent,
            "failed": failed,
            "messages": [receipt.message.id for receipt in receipts],
            "delivery_channel": delivery_channel,
        }

    @app.get("/campaigns/{campaign_id}/score")
    def get_campaign_score(
        campaign_id: int,
        content: str | None = None,
        sender: str | None = None,
        html: bool = False,
        repo: LedgerRepository = Depends(get_repository),
        deliverability: DeliverabilityService = Depends(get_deliverability_service),
    ) -> dict[str, Any]:
        campaign = repo.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        return deliverability.score_campaign(campaign_id, content=content, html=html, sender=sender).to_dict()

    @app.post("/campaigns/{campaign_id}/predict")
    def predict_campaign(
        campaign_id: int,
        payload: PredictRequest,
        repo: LedgerRepository = Depends(get_repository),
        deliverability: DeliverabilityService = Depends(get_deliverability_service),
    ) -> dict[str, Any]:
        campaign = repo.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        return deliverability.predict(
            _score_content(payload.subject, payload.content),
            payload.recipients,
            sender=payload.sender,
            html=payload.html,
        ).to_dict()

    @app.get("/campaigns/{campaign_id}")
    def get_campaign(campaign_id: int, repo: LedgerRepository = Depends(get_repository)) -> dict[str, Any]:
        campaign = repo.get_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        rollups = repo.recipient_status_rollups(campaign_id)
        return {
            "id": campaign.id,
            "name": campaign.name,
            "status_rollups": {status.value: rollups.get(status, 0) for status in Status},
        }

    # ------------------------------------------------------------------ compose
    @app.post("/compose/preview")
    def compose_preview(payload: PreviewRequest, logger: LoggingService = Depends(get_logging_service)) -> dict[str, Any]:
        rendered = autograb.render(payload.template, payload.email)
        context = autograb.context_from_email(payload.email)
        logger.info(LogComponent.AUTOGRAB, "autograb preview rendered", email=payload.email, html=payload.html, fields=sorted(context.keys()))
        return {"rendered": rendered, "context": context, "html": payload.html}

    @app.post("/compose/analyze")
    def compose_analyze(payload: AnalyzeRequest) -> dict[str, Any]:
        return analyze_compose(payload.content, html=payload.html)

    # ------------------------------------------------------------------ warmup
    @app.post("/warmup/domains", status_code=201)
    def configure_warmup_domain(
        payload: WarmupDomainRequest,
        warmup: WarmupService = Depends(get_warmup_service),
    ) -> dict[str, Any]:
        config = warmup.enable_domain(
            payload.domain,
            WarmupConfig(
                daily_limit=payload.daily_limit,
                max_per_batch=payload.max_per_batch,
                max_per_hour=payload.max_per_hour,
                ramp_start_limit=payload.ramp_start_limit,
                ramp_days=payload.ramp_days,
                enabled=payload.enabled,
            ),
        )
        return {"domain": payload.domain.strip().lower(), "config": config.to_dict()}

    @app.get("/warmup/domains")
    def list_warmup_domains(warmup: WarmupService = Depends(get_warmup_service)) -> dict[str, Any]:
        return {"domains": warmup.list_domains()}

    @app.get("/warmup/domains/{domain}/status")
    def get_warmup_status(domain: str, warmup: WarmupService = Depends(get_warmup_service)) -> dict[str, Any]:
        try:
            return warmup.progress(domain).to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/warmup/domains/{domain}/override")
    def override_warmup_domain(
        domain: str,
        payload: WarmupOverrideRequest,
        warmup: WarmupService = Depends(get_warmup_service),
    ) -> dict[str, Any]:
        try:
            config = warmup.admin_override(
                domain,
                authorized=payload.authorized,
                daily_limit=payload.daily_limit,
                max_per_hour=payload.max_per_hour,
                max_per_batch=payload.max_per_batch,
                bypass_remaining=payload.bypass_remaining,
                detail=payload.detail,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"domain": domain.strip().lower(), "config": config.to_dict()}

    @app.get("/warmup/domains/{domain}/events")
    def get_warmup_events(domain: str, warmup: WarmupService = Depends(get_warmup_service)) -> dict[str, Any]:
        return {"domain": domain.strip().lower(), "events": warmup.events(domain)}

    # ------------------------------------------------------------------ domains
    @app.get("/domains")
    def list_domains(domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        return {"domains": [domain.to_dict() for domain in domains.list_domains()]}

    @app.post("/domains", status_code=201)
    def add_domain(payload: DomainCreate, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        try:
            domain = domains.add_domain(
                payload.name,
                selector=payload.selector,
                dmarc_policy=payload.dmarc_policy,
                spf_includes=payload.spf_includes,
            )
        except DomainError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _domain_payload(domains, domain)

    @app.get("/domains/{domain_id}")
    def get_domain(domain_id: int, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        domain = domains.get_domain(domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return _domain_payload(domains, domain)

    @app.post("/domains/{domain_id}/verify")
    def verify_domain(domain_id: int, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        try:
            domain = domains.verify_domain(domain_id)
        except DomainError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _domain_payload(domains, domain)

    @app.patch("/domains/{domain_id}/dmarc")
    def update_dmarc(
        domain_id: int, payload: DmarcPolicyUpdate, domains: DomainService = Depends(get_domain_service)
    ) -> dict[str, Any]:
        try:
            domain = domains.update_dmarc_policy(domain_id, payload.policy)
        except DomainError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _domain_payload(domains, domain)

    @app.post("/domains/{domain_id}/dkim/rotate")
    def rotate_dkim(domain_id: int, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        try:
            domain = domains.regenerate_dkim(domain_id)
        except DomainError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _domain_payload(domains, domain)

    @app.delete("/domains/{domain_id}", status_code=200)
    def delete_domain(domain_id: int, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        if not domains.delete_domain(domain_id):
            raise HTTPException(status_code=404, detail="domain not found")
        return {"deleted": True, "id": domain_id}

    @app.get("/domains/{domain_id}/history")
    def domain_history(domain_id: int, domains: DomainService = Depends(get_domain_service)) -> dict[str, Any]:
        domain = domains.get_domain(domain_id)
        if domain is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return {"id": domain_id, "history": domains.repository.health_history(domain_id)}

    return app


def _score_content(subject: str, content: str) -> str:
    subject = subject.strip()
    return f"{subject}\n\n{content}" if subject else content


def _domain_payload(domains: DomainService, domain: Any) -> dict[str, Any]:
    data = domain.to_dict()
    data["records"] = [record.to_dict() for record in domains.required_records(domain)]
    return data


def _enforce_domain(domains: DomainService, sender: str, enforce: bool) -> None:
    if not enforce:
        return
    sender_domain = _sender_domain(sender)
    if sender_domain is None:
        return
    registered = domains.get_domain_by_name(sender_domain)
    # Backward compatible: only enforce verification once the domain is managed.
    if registered is not None and not registered.is_verified:
        raise HTTPException(
            status_code=400,
            detail=f"sender domain '{sender_domain}' is not verified; verify DKIM/SPF/DMARC before sending",
        )


app = create_app()
