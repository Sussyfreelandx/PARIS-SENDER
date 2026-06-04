"""Standalone autograb and Jinja2 rendering helpers preserved from the monolith."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from jinja2 import Environment, TemplateError

COMMON_ISP_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "aol.com",
    "icloud.com",
    "comcast.net",
    "verizon.net",
    "att.net",
    "cox.net",
    "sbcglobal.net",
    "bellsouth.net",
    "earthlink.net",
    "charter.net",
}

GENERIC_WORDS = {
    "info",
    "contact",
    "admin",
    "support",
    "sales",
    "mail",
    "email",
    "hello",
    "test",
    "demo",
    "user",
    "customer",
    "press",
    "jobs",
    "careers",
    "service",
    "team",
    "office",
    "billing",
    "accounts",
    "dev",
    "webmaster",
    "media",
    "noreply",
    "no-reply",
    "marketing",
    "newsletter",
    "updates",
    "general",
    "enquiry",
    "staff",
    "manager",
    "hr",
    "recruitment",
    "inquiries",
}


def derive_personalization_context(
    email: str,
    existing: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    sender_name: str = "Sender",
) -> dict[str, Any]:
    """Derive firstname, greetings, company, and related fields from an email address."""
    current = now or datetime.now()
    context = {k.lower(): v for k, v in (existing or {}).items()}
    context.setdefault("email", email)

    local_part, domain = _split_email(email)
    found_name = context.get("firstname")
    if not found_name:
        valid_parts = _valid_name_parts(local_part)
        found_name = valid_parts[0].capitalize() if valid_parts else "User"
        context["firstname"] = found_name

    found_company = context.get("company")
    if not found_company:
        found_company = _derive_company(domain)
        if found_company:
            context["company"] = found_company

    base_greeting = _base_greeting(current.hour)
    context["greetings"] = f"{base_greeting} {found_name}" if found_name else base_greeting
    context.setdefault("firstname", "Hello")
    context.setdefault("company", "you")
    context.setdefault("domain", domain)
    context.setdefault("uname", local_part)
    context.setdefault("sender_name", sender_name)
    context.setdefault("currentdate", current.strftime("%B %d, %Y"))
    context.setdefault("date", current.strftime("%m/%d/%Y"))
    context.setdefault("time", current.strftime("%I:%M %p"))
    return context


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render Jinja2 plus legacy [placeholder] syntax using the supplied context."""
    env = Environment(autoescape=False)
    prepared = re.sub(r"\[([a-zA-Z0-9_]+)\]", lambda m: "{{ " + m.group(1).lower() + " }}", template)
    try:
        return env.from_string(prepared).render(context)
    except TemplateError:
        rendered = prepared
        for key, value in context.items():
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
        return rendered


class AutograbService:
    """Small service wrapper for personalization and rendering."""

    def context_from_email(self, email: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        return derive_personalization_context(email, existing)

    def render(self, template: str, email: str, existing: dict[str, Any] | None = None) -> str:
        return render_template(template, self.context_from_email(email, existing))


def _split_email(email: str) -> tuple[str, str]:
    if "@" not in email:
        return email, "example.com"
    local_part, domain = email.split("@", 1)
    return local_part, domain.lower()


def _valid_name_parts(local_part: str) -> list[str]:
    parts = re.split(r"[._\-+]+", local_part)
    return [part for part in parts if len(part) > 1 and part.isalpha()]


def _derive_company(domain: str) -> str:
    if domain in COMMON_ISP_DOMAINS:
        return "you"
    parts = domain.split(".")
    if len(parts) > 2 and len(parts[-2]) > 2 and parts[-2] not in {"co", "com", "org", "net", "ac", "gov", "edu"}:
        company_part = parts[-2]
    else:
        company_part = parts[0]
    return "-".join(part.capitalize() for part in company_part.split("-"))


def _base_greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"
