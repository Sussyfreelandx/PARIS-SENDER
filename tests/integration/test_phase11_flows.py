from __future__ import annotations

import pytest

from backend.api import create_app
from backend.models import LogComponent, WarmupConfig
from backend.repositories import LedgerRepository
from backend.services import DeliverabilityService
from tests.conftest import FakeProvider, FakeResolver


@pytest.mark.integration
def test_campaign_create_send_updates_ledger(client, app_bundle):
    created = client.post("/campaigns", json={"name": "Flow"})
    campaign_id = created.json()["id"]

    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com"], "subject": "Hi", "content": "Requested update", "sender": "sender@example.com"},
    )

    assert sent.status_code == 200
    assert sent.json()["sent"] == 2
    assert len(app_bundle.provider.messages) == 2
    assert client.get(f"/campaigns/{campaign_id}").json()["status_rollups"]["SENT"] == 2


@pytest.mark.integration
def test_domain_onboarding_dns_verify_then_managed_send_allowed(client, app_bundle):
    created = client.post("/domains", json={"name": "example.com", "selector": "paris"})
    assert created.status_code == 201
    domain_payload = created.json()
    app_bundle.domains.resolver.records = {record["host"]: [record["value"]] for record in domain_payload["records"]}

    verified = client.post(f"/domains/{domain_payload['id']}/verify")
    assert verified.status_code == 200
    assert verified.json()["is_verified"] is True

    campaign_id = client.post("/campaigns", json={"name": "Verified"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Requested update", "sender": "sender@example.com"},
    )
    assert sent.status_code == 200


@pytest.mark.integration
def test_warmup_enforcement_blocks_active_send(client, app_bundle):
    app_bundle.warmup.enable_domain("example.com", WarmupConfig(daily_limit=1, max_per_batch=1, max_per_hour=1, ramp_start_limit=1, ramp_days=1))
    campaign_id = client.post("/campaigns", json={"name": "Warmup"}).json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com", "b@example.com"], "subject": "Hi", "content": "Requested update", "sender": "sender@example.com"},
    )

    assert response.status_code == 400
    assert "warmup limit" in response.json()["detail"]
    assert app_bundle.provider.messages == []


@pytest.mark.integration
def test_deliverability_scoring_gates_send_endpoint(ledger_repo, fake_provider, domain_service, log_service):
    app = create_app(
        repository=ledger_repo,
        provider=fake_provider,
        domain_service=domain_service,
        logging_service=log_service,
        deliverability_service=DeliverabilityService(ledger_repo, domain_service, threshold=95, logger=log_service),
        enforce_verified_domains=False,
    )
    from fastapi.testclient import TestClient

    client = TestClient(app)
    campaign_id = client.post("/campaigns", json={"name": "Gate"}).json()["id"]
    blocked = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["bad-address", "bad-address"], "subject": "FREE winner", "content": "urgent lottery cash offer", "sender": "sender@example.com"},
    )

    assert blocked.status_code == 400
    assert "deliverability score" in blocked.json()["detail"]
    assert fake_provider.messages == []


@pytest.mark.integration
def test_logging_emitted_across_services_and_redacted(client, app_bundle):
    campaign_id = client.post("/campaigns", json={"name": "Logs"}).json()["id"]
    response = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Requested update", "sender": "sender@example.com", "non_smtp_delivery": True},
    )
    assert response.status_code == 200
    app_bundle.logger.error(LogComponent.API, "operator supplied ******", token="secret-token")

    logs = client.get("/logs", params={"limit": 20}).json()["logs"]
    components = {entry["component"] for entry in logs}
    serialized = str(logs)

    assert {"CAMPAIGN", "DELIVERABILITY", "DELIVERY", "API"}.issubset(components)
    assert "hunter2" not in serialized and "secret-token" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.integration
def test_regression_autograb_ledger_shape_and_delivery_consistency(client, app_bundle):
    preview = client.post("/compose/preview", json={"template": "Hi [firstname] at [domain]", "email": "jane@example.com"})
    assert preview.status_code == 200
    assert preview.json()["rendered"] == "Hi Jane at example.com"

    campaign_id = client.post("/campaigns", json={"name": "Regression"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["jane@example.com"], "subject": "Hi", "content": preview.json()["rendered"], "sender": "sender@example.com"},
    )
    message_id = sent.json()["messages"][0]
    events = app_bundle.repo.list_events_for_message(message_id)

    assert [event.status.value for event in events] == ["QUEUED", "PROCESSING", "SENT"]
    assert app_bundle.repo.get_message(message_id).provider_message_id == "fake-1"
