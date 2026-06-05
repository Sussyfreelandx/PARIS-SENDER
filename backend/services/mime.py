"""Single MIME construction helper for all delivery providers."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


@dataclass(slots=True)
class Attachment:
    """A file attachment carried alongside an outbound message.

    ``content`` holds the raw (already-decoded) file bytes. Use
    :meth:`from_base64` to build one from the base64 payload the renderer sends
    over the API.
    """

    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"

    @classmethod
    def from_base64(cls, filename: str, content_base64: str, mime_type: str | None = None) -> "Attachment":
        """Build an attachment from a base64-encoded payload (as sent by the UI)."""
        cleaned = (filename or "attachment").strip() or "attachment"
        try:
            raw = base64.b64decode(content_base64 or "", validate=True)
        except Exception as exc:  # noqa: BLE001 - surface a clear, actionable error
            raise ValueError(f"attachment {cleaned!r} has invalid base64 content") from exc
        normalized_type = (mime_type or "application/octet-stream").strip() or "application/octet-stream"
        return cls(filename=cleaned, content=raw, mime_type=normalized_type)


def _attach_file(message: MIMEMultipart, attachment: Attachment) -> None:
    maintype, _, subtype = attachment.mime_type.partition("/")
    part = MIMEApplication(attachment.content, _subtype=subtype or "octet-stream")
    # Reset the default application/octet-stream type when a richer type is known.
    if maintype and subtype:
        part.replace_header("Content-Type", f"{maintype}/{subtype}")
    part.add_header("Content-Disposition", "attachment", filename=attachment.filename)
    message.attach(part)


def build_mime_message(
    sender: str,
    recipient: str,
    subject: str,
    content: str,
    *,
    html: bool = False,
    attachments: Iterable[Attachment] | None = None,
) -> MIMEMultipart:
    """Build an RFC-compatible MIME message in one shared location.

    When ``attachments`` are supplied the message is wrapped in a ``multipart/
    mixed`` container: the text/HTML body becomes a nested ``multipart/
    alternative`` part and each attachment is added as a separate part with a
    ``Content-Disposition: attachment`` header.
    """
    subtype = "html" if html else "plain"
    body = MIMEText(content, subtype, "utf-8")

    attachment_list = list(attachments or [])
    if not attachment_list:
        message = MIMEMultipart("alternative")
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.attach(body)
        return message

    message = MIMEMultipart("mixed")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    alternative = MIMEMultipart("alternative")
    alternative.attach(body)
    message.attach(alternative)
    for attachment in attachment_list:
        _attach_file(message, attachment)
    return message
