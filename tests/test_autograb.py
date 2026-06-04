from datetime import datetime

from backend.validators import derive_personalization_context, render_template


def test_context_preview_parity_cases():
    morning = datetime(2024, 1, 1, 9, 0)

    john = derive_personalization_context("john.doe@example.com", now=morning)
    assert john["firstname"] == "John"
    assert john["company"] == "Example"
    assert john["domain"] == "example.com"
    assert john["greetings"] == "Good morning John"

    admin = derive_personalization_context("admin@company.org", now=morning)
    assert admin["firstname"] == "Admin"
    assert admin["company"] == "Company"
    assert admin["greetings"] == "Good morning Admin"

    numeric = derive_personalization_context("12345@numbers.com", now=morning)
    assert numeric["firstname"] == "User"
    assert numeric["company"] == "Numbers"
    assert numeric["greetings"] == "Good morning User"


def test_isp_domain_company_fallback():
    context = derive_personalization_context("john@gmail.com", now=datetime(2024, 1, 1, 9, 0))
    assert context["firstname"] == "John"
    assert context["company"] == "you"


def test_jinja2_and_legacy_rendering():
    context = derive_personalization_context("john.doe@example.com", now=datetime(2024, 1, 1, 9, 0))
    rendered = render_template("{{ greetings }}, [FIRSTNAME] from {{ company }}", context)
    assert rendered == "Good morning John, John from Example"
