"""Centralized logging service and archiving helpers."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.models import LogComponent, LogEntry, LogSeverity
from backend.repositories import LogRepository

Clock = Callable[[], datetime]
AlertSink = Callable[[LogEntry], None]

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "dkim_private_key",
    "password",
    "secret",
    "smtp_pass",
    "smtp_password",
    "token",
}
_SENSITIVE_MESSAGE_RE = re.compile(
    r"(?i)(password|smtp_pass|smtp_password|token|secret|api[_-]?key|authorization)(\s*[=:]?\s*)([^\s,;]+)"
)


class LoggingService:
    """Persist structured backend logs and expose query, summary, and archiving helpers."""

    def __init__(
        self,
        repository: LogRepository | None = None,
        clock: Clock | None = None,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self.repository = repository or LogRepository(":memory:")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.alert_sink = alert_sink

    def log(self, component: LogComponent | str, severity: LogSeverity | str, message: str, **context: Any) -> LogEntry:
        """Persist a structured log entry and return the stored entry."""
        entry = LogEntry(
            id=None,
            timestamp=self._now(),
            severity=self._coerce_severity(severity),
            component=self._coerce_component(component),
            message=self._redact_message(message),
            context=self._redact_context(context),
        )
        stored = self.repository.append(entry)
        saved = LogEntry(
            id=int(stored["id"]),
            timestamp=datetime.fromisoformat(stored["timestamp"]),
            severity=LogSeverity(stored["severity"]),
            component=LogComponent(stored["component"]),
            message=stored["message"],
            context=stored["context"],
        )
        if self.alert_sink is not None and saved.severity in {LogSeverity.ERROR, LogSeverity.CRITICAL}:
            self.alert_sink(saved)
        return saved

    def debug(self, component: LogComponent | str, message: str, **context: Any) -> LogEntry:
        """Persist a DEBUG log entry."""
        return self.log(component, LogSeverity.DEBUG, message, **context)

    def info(self, component: LogComponent | str, message: str, **context: Any) -> LogEntry:
        """Persist an INFO log entry."""
        return self.log(component, LogSeverity.INFO, message, **context)

    def warning(self, component: LogComponent | str, message: str, **context: Any) -> LogEntry:
        """Persist a WARNING log entry."""
        return self.log(component, LogSeverity.WARNING, message, **context)

    def error(self, component: LogComponent | str, message: str, **context: Any) -> LogEntry:
        """Persist an ERROR log entry."""
        return self.log(component, LogSeverity.ERROR, message, **context)

    def critical(self, component: LogComponent | str, message: str, **context: Any) -> LogEntry:
        """Persist a CRITICAL log entry."""
        return self.log(component, LogSeverity.CRITICAL, message, **context)

    def query(
        self,
        *,
        severity: LogSeverity | str | None = None,
        component: LogComponent | str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query persisted logs with optional filters."""
        return self.repository.query(
            severity=severity,
            component=component,
            since=self._coerce_datetime(since),
            until=self._coerce_datetime(until),
            limit=limit,
        )

    def summary(self) -> dict[str, Any]:
        """Return log analytics summary data."""
        return self.repository.summary()

    def archive(self, before: datetime) -> int:
        """Archive logs older than the supplied cutoff."""
        return self.repository.archive(before)

    async def run_archiver(
        self,
        *,
        interval_seconds: float = 3600.0,
        max_age_seconds: float = 604800.0,
        stop: asyncio.Event | None = None,
    ) -> None:
        """Periodically archive logs older than max_age_seconds until stopped."""
        while stop is None or not stop.is_set():
            self.archive(self._now() - timedelta(seconds=max_age_seconds))
            try:
                if stop is None:
                    await asyncio.sleep(interval_seconds)
                else:
                    await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _redact_context(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: _REDACTED if self._is_sensitive_key(str(key)) else self._redact_context(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_context(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_context(item) for item in value)
        return value

    def _redact_message(self, message: str) -> str:
        return _SENSITIVE_MESSAGE_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}", message)

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_")
        return normalized in _SENSITIVE_KEYS or any(part in normalized for part in ("password", "secret", "token"))

    def _coerce_severity(self, value: LogSeverity | str) -> LogSeverity:
        return value if isinstance(value, LogSeverity) else LogSeverity(str(value).strip().upper())

    def _coerce_component(self, value: LogComponent | str) -> LogComponent:
        return value if isinstance(value, LogComponent) else LogComponent(str(value).strip().upper())

    def _coerce_datetime(self, value: datetime | str | None) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)


def start_log_archiver(
    service: LoggingService,
    *,
    interval_seconds: float = 3600.0,
    max_age_seconds: float = 604800.0,
) -> asyncio.Task[None]:
    """Start the opt-in log archiver in a running event loop."""
    stop = asyncio.Event()
    task = asyncio.create_task(service.run_archiver(interval_seconds=interval_seconds, max_age_seconds=max_age_seconds, stop=stop))
    setattr(task, "log_archiver_stop_event", stop)
    return task


async def stop_log_archiver(task: asyncio.Task[None] | None) -> None:
    """Stop an archiver task created by start_log_archiver."""
    if task is None:
        return
    stop = getattr(task, "log_archiver_stop_event", None)
    if stop is not None:
        stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
