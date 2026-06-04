from backend.models import Campaign, Event, Message, Status
from backend.repositories import LedgerRepository


def test_schema_creation_and_campaign_roundtrip():
    repo = LedgerRepository(":memory:")
    campaign = repo.create_campaign("Launch")

    assert campaign.id is not None
    loaded = repo.get_campaign(campaign.id)
    assert loaded is not None
    assert loaded.name == "Launch"


def test_recording_each_status_persists_events_and_rollups():
    repo = LedgerRepository(":memory:")
    campaign = repo.create_campaign(Campaign("Statuses"))
    recipient = repo.add_recipient(campaign.id, "john.doe@example.com")
    message = repo.create_message(Message(campaign.id, recipient.id, "Subject", "Body"))

    for status in Status:
        repo.record_event(Event(message_id=message.id, status=status))

    events = repo.list_events_for_message(message.id)
    assert [event.status for event in events] == list(Status)
    assert len(events) == len(Status)
    loaded = repo.get_message(message.id)
    assert loaded is not None
    assert loaded.status == Status.UNSUBSCRIBED
    assert repo.recipient_status_rollups(campaign.id)[Status.UNSUBSCRIBED] == 1


def test_event_subtype_helpers_are_persisted():
    repo = LedgerRepository(":memory:")
    campaign = repo.create_campaign("Tracking")
    recipient = repo.add_recipient(campaign.id, "visitor@example.com")
    message = repo.create_message(Message(campaign.id, recipient.id, "Subject", "Body"))

    repo.record_event(Event.open(message.id))
    repo.record_event(Event.click(message.id, url="https://example.com"))
    repo.record_event(Event.bounce(message.id, error="550"))
    repo.record_event(Event.unsubscribe(message.id))

    events = repo.list_events_for_message(message.id)
    assert [event.status for event in events] == [Status.OPENED, Status.CLICKED, Status.BOUNCED, Status.UNSUBSCRIBED]
    assert events[1].url == "https://example.com"
    assert events[2].error == "550"
