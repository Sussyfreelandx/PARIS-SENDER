"""Compose-time content analysis: placeholder/Jinja validation, spam & HTML ratio.

These helpers power the Electron Compose editor (Phase 2). They surface
warnings rather than block sending, preserving the retired desktop compose
checks in a UI-agnostic, testable form.
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, TemplateSyntaxError

# Known autograb placeholders that resolve via derive_personalization_context.
KNOWN_PLACEHOLDERS = {
    "firstname",
    "greetings",
    "company",
    "email",
    "domain",
    "uname",
    "sender_name",
    "currentdate",
    "date",
    "time",
}

# Lightweight spam-trigger lexicon (subset of common spam-filter words).
SPAM_WORDS = {
    "free",
    "winner",
    "guarantee",
    "guaranteed",
    "urgent",
    "act now",
    "click here",
    "buy now",
    "limited time",
    "cash",
    "credit",
    "cheap",
    "discount",
    "offer",
    "risk-free",
    "100%",
    "no cost",
    "viagra",
    "lottery",
    "congratulations",
    "earn money",
    "double your",
}

_BRACKET_RE = re.compile(r"\[([a-zA-Z0-9_]+)\]")
_JINJA_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(content: str) -> str:
    """Return the visible text of an HTML fragment."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def find_placeholders(content: str) -> list[str]:
    """Return all [bracket] and {{ jinja }} variable names referenced in content."""
    names = {m.group(1).lower() for m in _BRACKET_RE.finditer(content)}
    names |= {m.group(1).lower() for m in _JINJA_VAR_RE.finditer(content)}
    return sorted(names)


def validate_jinja(content: str) -> list[str]:
    """Return Jinja2 syntax error messages (empty list when valid)."""
    prepared = _BRACKET_RE.sub(lambda m: "{{ " + m.group(1).lower() + " }}", content)
    try:
        Environment(autoescape=False).parse(prepared)
    except TemplateSyntaxError as exc:
        return [f"Jinja syntax error: {exc.message} (line {exc.lineno})"]
    return []


def unknown_placeholders(content: str) -> list[str]:
    """Return referenced placeholders that are not part of the autograb context."""
    return [name for name in find_placeholders(content) if name not in KNOWN_PLACEHOLDERS]


def spam_hits(content: str) -> list[str]:
    """Return the spam-trigger words/phrases found in the content."""
    lowered = content.lower()
    return sorted({word for word in SPAM_WORDS if word in lowered})


def html_text_ratio(content: str, *, html: bool) -> float:
    """Return the ratio of visible text length to total markup length (0..1)."""
    if not html:
        return 1.0
    total = len(content)
    if total == 0:
        return 0.0
    text_len = len(strip_html(content))
    return round(text_len / total, 3)


def analyze_compose(content: str, *, html: bool = False) -> dict[str, Any]:
    """Produce a compose-time analysis report with validation and spam warnings."""
    text = strip_html(content) if html else content
    placeholders = find_placeholders(content)
    unknown = unknown_placeholders(content)
    jinja_errors = validate_jinja(content)
    spam = spam_hits(text)
    ratio = html_text_ratio(content, html=html)

    warnings: list[str] = []
    warnings.extend(jinja_errors)
    if unknown:
        warnings.append("Unknown placeholders: " + ", ".join(unknown))
    if spam:
        warnings.append("Spam-trigger words present: " + ", ".join(spam))
    if html and ratio < 0.10:
        warnings.append("Low text-to-HTML ratio; add more plain text to reduce spam risk")
    if html and "unsubscribe" not in text.lower():
        warnings.append("No visible unsubscribe text found")

    return {
        "char_count": len(content),
        "text_char_count": len(text),
        "placeholders": placeholders,
        "unknown_placeholders": unknown,
        "jinja_errors": jinja_errors,
        "spam_words": spam,
        "spam_score": len(spam),
        "html_text_ratio": ratio,
        "warnings": warnings,
        "valid": not jinja_errors,
    }
