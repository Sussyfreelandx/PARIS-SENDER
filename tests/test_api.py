from backend.api import create_app
from backend.repositories import LedgerRepository
from backend.services import DeliveryProvider, DeliveryResult


class FakeProvider(DeliveryProvider):
    def send(self, message):
        return DeliveryResult(True, provider_message_id="api-id")


def test_api_create_send_status_and_health():
    from fastapi.testclient import TestClient

    repo = LedgerRepository(":memory:")
    app = create_app(repository=repo, provider=FakeProvider())
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    created = client.post("/campaigns", json={"name": "API Campaign"})
    assert created.status_code == 201
    campaign_id = created.json()["id"]

    sent = client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Body", "sender": "s@example.com"},
    )
    assert sent.status_code == 200
    assert sent.json()["sent"] == 1

    status = client.get(f"/campaigns/{campaign_id}")
    assert status.status_code == 200
    assert status.json()["status_rollups"]["SENT"] == 1


def test_api_list_and_delete_campaign():
    from fastapi.testclient import TestClient

    repo = LedgerRepository(":memory:")
    app = create_app(repository=repo, provider=FakeProvider())
    client = TestClient(app)

    campaign_id = client.post("/campaigns", json={"name": "Deletable"}).json()["id"]
    client.post(
        f"/campaigns/{campaign_id}/send",
        json={"recipients": ["a@example.com"], "subject": "Hi", "content": "Body", "sender": "s@example.com"},
    )

    listing = client.get("/campaigns")
    assert listing.status_code == 200
    assert any(item["id"] == campaign_id for item in listing.json()["campaigns"])

    deleted = client.delete(f"/campaigns/{campaign_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "campaign_id": campaign_id}

    assert client.get(f"/campaigns/{campaign_id}").status_code == 404
    assert client.delete(f"/campaigns/{campaign_id}").status_code == 404
    assert all(item["id"] != campaign_id for item in client.get("/campaigns").json()["campaigns"])


def test_root_and_favicon_routes_do_not_404():
    from fastapi.testclient import TestClient

    app = create_app(repository=LedgerRepository(":memory:"), provider=FakeProvider())
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    assert "Paris Sender backend is running" in root.text

    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 204
