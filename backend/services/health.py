"""Aggregated health monitor for delivery, queue, domain, and server status."""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from backend.models import ComponentHealth, DomainHealthSummary, HealthServer, HealthStatus, LogComponent, QueueDepth, ServerHealth, Status
from backend.repositories import LedgerRepository
from backend.services.domain import DomainService
from backend.services.warmup import WarmupService

Clock = Callable[[], datetime]

_STATUS_SEVERITY = {
    HealthStatus.OK: 0,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.WARN: 2,
    HealthStatus.CRITICAL: 3,
}


class ServerProbe(Protocol):
    """Callable seam for deterministic server health probes."""

    def __call__(self, server: HealthServer, *, now: datetime) -> ComponentHealth | ServerHealth:
        """Return health for a configured server without mutating state."""
        ...


class HealthMonitorService:
    """Aggregates monitor data across servers, domains, warmup, ledger, and non-SMTP delivery."""

    def __init__(
        self,
        *,
        ledger: LedgerRepository | None = None,
        domain_service: DomainService | None = None,
        warmup_service: WarmupService | None = None,
        servers: Iterable[HealthServer | dict[str, Any]] | None = None,
        smtp_probe: ServerProbe | None = None,
        mx_probe: ServerProbe | None = None,
        server_probe: ServerProbe | None = None,
        clock: Clock | None = None,
        throughput_window_seconds: int = 300,
        logger: Any | None = None,
    ) -> None:
        self.ledger = ledger
        self.domain_service = domain_service
        self.warmup_service = warmup_service
        self.servers = [self._coerce_server(server) for server in servers or []]
        self.smtp_probe = smtp_probe or self._not_configured_probe
        self.mx_probe = mx_probe or self._not_configured_probe
        self.server_probe = server_probe or self._not_configured_probe
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.throughput_window_seconds = max(1, int(throughput_window_seconds))
        self.logger = logger
        self._latest_snapshot: dict[str, Any] | None = None

    @property
    def latest_snapshot(self) -> dict[str, Any] | None:
        """Return the latest cached snapshot, if the async monitor has populated one."""
        return self._latest_snapshot

    def snapshot(self) -> dict[str, Any]:
        """Build and cache a complete health snapshot."""
        now = self._now()
        queue_depth = self._queue_depth()
        throughput = self._throughput(now)
        servers = [self._probe_server(server, now) for server in self.servers]
        domain_summaries = self._domain_summaries()
        domain_alerts = [summary for summary in domain_summaries if summary.status in {HealthStatus.WARN, HealthStatus.CRITICAL}]
        components = [
            self._queue_component(queue_depth, now),
            self._non_smtp_component(queue_depth, throughput, now),
            self._domain_component(domain_summaries, now),
            self._warmup_component(now),
            *servers,
        ]
        overall = self._worst(component.status for component in components)
        snapshot = {
            "overall_status": overall.value,
            "generated_at": now.isoformat(),
            "components": [component.to_dict() for component in components],
            "queue_depth": queue_depth.to_dict(),
            "throughput": throughput,
            "domain_alerts": [summary.to_dict() for summary in domain_alerts],
            "domains": [summary.to_dict() for summary in domain_summaries],
            "servers": [server.to_dict() for server in servers],
        }
        self._latest_snapshot = snapshot
        self._log(
            "ERROR" if overall is HealthStatus.CRITICAL else "WARNING" if overall is HealthStatus.WARN else "INFO",
            "health snapshot generated",
            overall_status=overall.value,
            components=len(components),
            queue_depth=queue_depth.to_dict(),
            domain_alerts=len(domain_alerts),
        )
        return snapshot

    aggregate = snapshot

    def domain_health(self, domain: str) -> dict[str, Any]:
        """Return detailed DKIM/SPF/DMARC health for a managed domain."""
        if self.domain_service is None:
            raise ValueError("domain health service is not configured")
        normalized = domain.strip().lower()
        domain_obj = self.domain_service.get_domain_by_name(normalized)
        if domain_obj is None:
            raise ValueError(f"domain {normalized} not found")
        summary = self._domain_summary(domain_obj)
        records = [record.to_dict() for record in self.domain_service.required_records(domain_obj)]
        data = summary.to_dict()
        data["records"] = records
        return data

    def server_health(self, server_id: str) -> dict[str, Any]:
        """Return health for a configured server target."""
        for server in self.servers:
            if server.id == server_id:
                return self._probe_server(server, self._now()).to_dict()
        raise ValueError(f"server {server_id} not found")

    async def run_monitor(self, *, interval_seconds: float = 30.0, stop: asyncio.Event | None = None) -> None:
        """Periodically refresh the cached snapshot until stopped."""
        while stop is None or not stop.is_set():
            self.snapshot()
            try:
                if stop is None:
                    await asyncio.sleep(interval_seconds)
                else:
                    await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    def _queue_depth(self) -> QueueDepth:
        if self.ledger is None:
            return QueueDepth()
        message_counts = self.ledger.status_counts(table="messages")
        recipient_counts = self.ledger.status_counts(table="recipients")
        return QueueDepth(
            queued_messages=message_counts.get(Status.QUEUED, 0),
            processing_messages=message_counts.get(Status.PROCESSING, 0),
            queued_recipients=recipient_counts.get(Status.QUEUED, 0),
            processing_recipients=recipient_counts.get(Status.PROCESSING, 0),
        )

    def _throughput(self, now: datetime) -> dict[str, Any]:
        since = now - timedelta(seconds=self.throughput_window_seconds)
        counts = self.ledger.event_status_counts_since(since) if self.ledger is not None else {status: 0 for status in Status}
        sent = counts.get(Status.SENT, 0) + counts.get(Status.DELIVERED, 0)
        failed = counts.get(Status.FAILED, 0) + counts.get(Status.BOUNCED, 0)
        return {
            "window_seconds": self.throughput_window_seconds,
            "since": since.isoformat(),
            "sent": sent,
            "failed": failed,
            "by_status": {status.value: counts.get(status, 0) for status in Status},
        }

    def _domain_summaries(self) -> list[DomainHealthSummary]:
        if self.domain_service is None:
            return []
        return [self._domain_summary(domain) for domain in self.domain_service.list_domains()]

    def _domain_summary(self, domain: Any) -> DomainHealthSummary:
        score = self.domain_service.health_score(domain) if self.domain_service is not None else int(domain.health_score)
        if domain.dkim_verified and domain.spf_verified and domain.dmarc_verified:
            status = HealthStatus.OK
            detail = "DKIM, SPF, and DMARC verified"
        elif score <= 0:
            status = HealthStatus.CRITICAL
            detail = "no authentication records verified"
        else:
            status = HealthStatus.WARN
            detail = "one or more authentication records are unverified"
        return DomainHealthSummary(
            domain=domain.name,
            status=status,
            health_score=score,
            dkim_verified=domain.dkim_verified,
            spf_verified=domain.spf_verified,
            dmarc_verified=domain.dmarc_verified,
            detail=detail,
            last_checked=domain.last_checked_at,
        )

    def _queue_component(self, queue_depth: QueueDepth, now: datetime) -> ComponentHealth:
        if queue_depth.total >= 10000:
            status = HealthStatus.CRITICAL
            detail = "delivery queue is critically backed up"
        elif queue_depth.total >= 1000:
            status = HealthStatus.WARN
            detail = "delivery queue is elevated"
        else:
            status = HealthStatus.OK
            detail = "queue depth is normal"
        return ComponentHealth("Delivery queue", "queue", status, detail, queue_depth.to_dict(), now)

    def _non_smtp_component(self, queue_depth: QueueDepth, throughput: dict[str, Any], now: datetime) -> ComponentHealth:
        failed = int(throughput.get("failed", 0))
        sent = int(throughput.get("sent", 0))
        status = HealthStatus.WARN if failed and failed >= sent else HealthStatus.OK
        detail = "non-SMTP path is represented by ledger throughput and active queue depth"
        return ComponentHealth(
            "Non-SMTP delivery path",
            "delivery",
            status,
            detail,
            {"active_depth": queue_depth.total, "sent": sent, "failed": failed},
            now,
        )

    def _domain_component(self, summaries: list[DomainHealthSummary], now: datetime) -> ComponentHealth:
        if self.domain_service is None:
            return ComponentHealth("Domain authentication", "domain", HealthStatus.UNKNOWN, "domain service not configured", {}, now)
        critical = sum(1 for summary in summaries if summary.status is HealthStatus.CRITICAL)
        warning = sum(1 for summary in summaries if summary.status is HealthStatus.WARN)
        status = HealthStatus.CRITICAL if critical else HealthStatus.WARN if warning else HealthStatus.OK
        detail = "domain authentication checks summarized"
        return ComponentHealth(
            "Domain authentication",
            "domain",
            status,
            detail,
            {"domains": len(summaries), "warnings": warning, "critical": critical},
            now,
        )

    def _warmup_component(self, now: datetime) -> ComponentHealth:
        if self.warmup_service is None:
            return ComponentHealth("Warmup campaigns", "warmup", HealthStatus.UNKNOWN, "warmup service not configured", {}, now)
        domains = self.warmup_service.list_domains()
        throttled: list[dict[str, Any]] = []
        for item in domains:
            domain = item.get("domain")
            if not domain:
                continue
            try:
                progress = self.warmup_service.progress(domain)
            except ValueError:
                continue
            if progress.throttled:
                throttled.append(progress.to_dict())
        status = HealthStatus.WARN if throttled else HealthStatus.OK
        return ComponentHealth(
            "Warmup campaigns",
            "warmup",
            status,
            "warmup domains throttled" if throttled else "warmup capacity available",
            {"in_progress": len(domains), "throttled": throttled},
            now,
        )

    def _probe_server(self, server: HealthServer, now: datetime) -> ServerHealth:
        probe = self._probe_for(server.kind)
        try:
            result = probe(server, now=now)
        except Exception as exc:
            result = ComponentHealth(server.id, server.kind, HealthStatus.CRITICAL, str(exc), {}, now)
        status = result.status if isinstance(result.status, HealthStatus) else HealthStatus(result.status)
        return ServerHealth(
            name=result.name or server.id,
            kind=result.kind or server.kind,
            status=status,
            detail=result.detail,
            metrics={**server.metadata, **result.metrics},
            last_checked=result.last_checked or now,
            server_id=getattr(result, "server_id", None) or server.id,
            host=getattr(result, "host", None) or server.host,
        )

    def _probe_for(self, kind: str) -> ServerProbe:
        normalized = kind.strip().lower()
        if normalized == "smtp":
            return self.smtp_probe
        if normalized == "mx":
            return self.mx_probe
        return self.server_probe

    def _not_configured_probe(self, server: HealthServer, *, now: datetime) -> ComponentHealth:
        return ComponentHealth(server.id, server.kind, HealthStatus.UNKNOWN, "probe not configured", {"host": server.host}, now)

    def _coerce_server(self, server: HealthServer | dict[str, Any]) -> HealthServer:
        if isinstance(server, HealthServer):
            return server
        return HealthServer(
            id=str(server["id"]),
            host=str(server.get("host", server["id"])),
            kind=str(server.get("kind", "server")),
            port=int(server["port"]) if server.get("port") is not None else None,
            metadata=dict(server.get("metadata", {})),
        )

    def _worst(self, statuses: Iterable[HealthStatus]) -> HealthStatus:
        return max(statuses, key=lambda status: _STATUS_SEVERITY[status], default=HealthStatus.OK)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _log(self, severity: str, message: str, **context: Any) -> None:
        if self.logger is not None:
            self.logger.log(LogComponent.HEALTH, severity, message, **context)


class SmtplibProbe:
    """Production SMTP probe; only performs network calls when explicitly injected."""

    def __call__(self, server: HealthServer, *, now: datetime) -> ComponentHealth:
        port = server.port or 25
        try:
            with smtplib.SMTP(server.host, port, timeout=float(server.metadata.get("timeout", 10))) as client:
                code, message = client.ehlo()
                tls_supported = client.has_extn("starttls")
            status = HealthStatus.OK if 200 <= int(code) < 400 else HealthStatus.WARN
            detail = "SMTP EHLO succeeded" if status is HealthStatus.OK else "SMTP EHLO returned non-success code"
            return ComponentHealth(
                server.id,
                server.kind,
                status,
                detail,
                {"host": server.host, "port": port, "ehlo_code": int(code), "ehlo": _decode_smtp_message(message), "tls": tls_supported},
                now,
            )
        except Exception as exc:
            return ComponentHealth(server.id, server.kind, HealthStatus.CRITICAL, str(exc), {"host": server.host, "port": port}, now)


def _decode_smtp_message(message: Any) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", "ignore")
    return str(message)


def start_health_monitor(service: HealthMonitorService, *, interval_seconds: float = 30.0) -> asyncio.Task[None]:
    """Start the opt-in health monitor in a running event loop."""
    stop = asyncio.Event()
    task = asyncio.create_task(service.run_monitor(interval_seconds=interval_seconds, stop=stop))
    setattr(task, "health_stop_event", stop)
    return task


async def stop_health_monitor(task: asyncio.Task[None] | None) -> None:
    """Stop a monitor task created by start_health_monitor."""
    if task is None:
        return
    stop = getattr(task, "health_stop_event", None)
    if stop is not None:
        stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
