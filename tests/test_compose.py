"""Tests for compose-time analysis used by the Electron Compose editor."""

from __future__ import annotations

from backend.validators.compose import analyze_compose, find_placeholders, spam_hits, validate_jinja


def test_find_placeholders_bracket_and_jinja():
    found = find_placeholders("Hi [firstname], {{ company }} welcomes you")
    assert "firstname" in found and "company" in found


def test_validate_jinja_detects_syntax_error():
    assert validate_jinja("Hello {{ firstname }}") == []
    errors = validate_jinja("Hello {{ firstname ")
    assert errors and "Jinja" in errors[0]


def test_spam_hits():
    assert "free" in spam_hits("Totally FREE offer")
    assert spam_hits("A normal message") == []


def test_analyze_compose_plain_text():
    report = analyze_compose("Hi [firstname], welcome", html=False)
    assert report["valid"] is True
    assert "firstname" in report["placeholders"]
    assert report["html_text_ratio"] == 1.0


def test_analyze_compose_flags_unknown_placeholder_and_spam():
    report = analyze_compose("Hi [bogusvar], FREE cash click here", html=False)
    assert "bogusvar" in report["unknown_placeholders"]
    assert report["spam_score"] >= 1
    assert any("Unknown placeholders" in w for w in report["warnings"])


def test_analyze_compose_html_low_ratio_warning():
    html = "<html><head><style>" + "x" * 500 + "</style></head><body>Hi</body></html>"
    report = analyze_compose(html, html=True)
    assert report["html_text_ratio"] < 0.10
    assert any("ratio" in w for w in report["warnings"])
    assert any("unsubscribe" in w.lower() for w in report["warnings"])
