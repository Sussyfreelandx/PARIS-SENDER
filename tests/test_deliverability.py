"""Unit tests for the deliverability score service."""

from __future__ import annotations

from backend.models import Campaign, Domain, DomainStatus, Event, EventType, Message, Status
from backend.repositories import DomainRepository, LedgerRepository
from backend.services import DeliverabilityService, DomainService


def _verified_domain_service() -> DomainService:
    service = DomainService(DomainRepository(":memory:"))
    service.repository.create(
        Domain(
            name="acme.com",
            status=DomainStatus.VERIFIED,
            dkim_verified=True,
            spf_verified=True,
            dmarc_verified=True,
            health_score=100,
        )
    )
    return service


def _campaign(repo: LedgerRepository) -> int:
    campaign = repo.create_campaign(Campaign("Deliverability"))
    assert campaign.id is not None
    return campaign.id


def test_components_and_overall_are_bounded() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo, _verified_domain_service())
    campaign_id = _campaign(repo)

    score = service.score_campaign(campaign_id, content="Hello team", sender="sender@acme.com")

    assert 0 <= score.score <= 100
    assert sum(component.weight for component in score.components) == 100
    assert {component.name for component in score.components} == {"domain", "history", "spam", "content", "engagement"}
    assert all(0 <= component.score <= 100 for component in score.components)


def test_clean_verified_campaign_scores_high() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo, _verified_domain_service())
    campaign_id = _campaign(repo)

    score = service.score_campaign(campaign_id, content="Hello, here is the update you requested.", sender="sender@acme.com")

    assert score.score >= 85
    assert score.passed is True


def test_spammy_unverified_campaign_scores_lower() -> None:
    repo = LedgerRepository(":memory:")
    domain_service = DomainService(DomainRepository(":memory:"))
    domain_service.repository.create(Domain(name="acme.com", status=DomainStatus.PENDING))
    service = DeliverabilityService(repo, domain_service)
    campaign_id = _campaign(repo)

    clean = service.score_campaign(campaign_id, content="Hello, here is the update.", sender="sender@acme.com")
    spammy = service.score_campaign(
        campaign_id,
        content="FREE winner urgent act now click here buy now lottery cash discount offer",
        sender="sender@acme.com",
    )

    assert spammy.score < clean.score
    assert spammy.components[0].score < 100


def test_empty_history_does_not_crash() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo)
    campaign_id = _campaign(repo)

    score = service.score_campaign(campaign_id, content="Body", sender="sender@example.com")

    assert 0 <= score.score <= 100


def test_autograb_placeholders_do_not_reduce_score() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo, _verified_domain_service())
    campaign_id = _campaign(repo)

    plain = service.score_campaign(campaign_id, content="Hello friend, welcome to our update.", sender="sender@acme.com")
    personalized = service.score_campaign(
        campaign_id,
        content="Hello [firstname], {{ greetings }} welcome to our update.",
        sender="sender@acme.com",
    )

    assert personalized.components[3].score >= plain.components[3].score
    assert personalized.score >= plain.score


def test_predict_works_without_persisted_history() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo, _verified_domain_service())

    score = service.predict("Hello [firstname]", ["a@example.com"], sender="sender@acme.com")

    assert 0 <= score.score <= 100
    assert score.threshold == 70


def test_non_smtp_path_produces_valid_score() -> None:
    repo = LedgerRepository(":memory:")
    service = DeliverabilityService(repo, _verified_domain_service())

    score = service.predict("Non SMTP body", ["a@example.com"], sender="sender@acme.com")

    assert 0 <= score.score <= 100


def test_history_and_spam_proxy_signals_affect_score() -> None:
    repo = LedgerRepository(":memory:")
    campaign_id = _campaign(repo)
    recipient = repo.add_recipient(campaign_id, "a@example.com")
    message = repo.create_message(Message(campaign_id, recipient.id, "Hi", "Body"))
    repo.record_event(Event(message.id, Status.BOUNCED, event_type=EventType.BOUNCE))
    service = DeliverabilityService(repo)

    score = service.score_campaign(campaign_id, content="Body", sender="sender@example.com")

    assert score.components[1].score < 75
    assert score.components[2].score < 85
