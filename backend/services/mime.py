"""Single MIME construction helper for all delivery providers."""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def build_mime_message(sender: str, recipient: str, subject: str, content: str, *, html: bool = False) -> MIMEMultipart:
    """Build an RFC-compatible MIME message in one shared location."""
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    subtype = "html" if html else "plain"
    message.attach(MIMEText(content, subtype, "utf-8"))
    return message
