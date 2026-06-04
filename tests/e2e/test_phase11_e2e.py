from __future__ import annotations

import pytest


@pytest.mark.e2e
def test_api_e2e_compose_preview_send_and_ledger_tracking(client, app_bundle):
    preview = client.post("/compose/preview", json={"template": "Hello [firstname], your update is ready.", "email": "alex@example.com"})
    assert preview.status_code == 200
    body = preview.json()["rendered"]
    analysis = client.post("/compose/analyze", json={"content": body})
    assert analysis.status_code == 200

    campaign_id = client.post("/campaigns", json={"name": "E2E Compose"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["alex@example.com"], "subject": "Update", "content": body, "sender": "sender@example.com"},
    )

    assert sent.status_code == 200
    message_id = sent.json()["messages"][0]
    assert client.get(f"/campaigns/{campaign_id}").json()["status_rollups"]["SENT"] == 1
    assert [event.status.value for event in app_bundle.repo.list_events_for_message(message_id)] == ["QUEUED", "PROCESSING", "SENT"]


@pytest.mark.e2e
def test_api_e2e_domain_create_verify_then_campaign_send(client, app_bundle):
    created = client.post("/domains", json={"name": "example.com"})
    records = created.json()["records"]
    app_bundle.domains.resolver.records = {record["host"]: [record["value"]] for record in records}

    verified = client.post(f"/domains/{created.json()['id']}/verify")
    assert verified.json()["status"] == "VERIFIED"

    campaign_id = client.post("/campaigns", json={"name": "E2E Domain"}).json()["id"]
    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hello", "content": "Requested update", "sender": "sender@example.com"},
    )
    assert sent.status_code == 200


@pytest.mark.e2e
def test_playwright_ui_smoke_optional():
    pytest.importorskip("playwright", reason="Playwright is optional and browser downloads are not part of the default test run")
