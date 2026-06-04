"""Tests for automatic logging redaction."""

from __future__ import annotations

from backend.models import LogComponent, LogSeverity
from backend.services import LoggingService


def test_logging_redacts_sensitive_context_and_message() -> None:
    service = LoggingService()

    entry = service.log(
        LogComponent.API,
        LogSeverity.INFO,
        "smtp ****** token abcdefghijklmnop",
        smtp_pass="supersecret",
        nested={"api_key": "abc123", "safe": "ok"},
        values=[{"authorization": "******"}],
    )

    assert "supersecret" not in entry.message
    assert "abcdefghijklmnop" not in entry.message
    assert entry.context["smtp_pass"] == "[REDACTED]"
    assert entry.context["nested"] == {"api_key": "[REDACTED]", "safe": "ok"}
    assert entry.context["values"] == [{"authorization": "[REDACTED]"}]
