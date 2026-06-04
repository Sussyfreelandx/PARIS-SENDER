from __future__ import annotations

import time

import pytest

from backend.models import Campaign, Message, Status
from backend.repositories import LedgerRepository, LogRepository, WarmupRepository
from backend.services import DeliveryService, LoggingService, WarmupService
from tests.conftest import FakeProvider, FixedClock, FakeResolver


@pytest.mark.performance
def test_campaign_bulk_processing_lightweight_benchmark():
    repo = LedgerRepository(":memory:")
    start = time.perf_counter()
    receipts = DeliveryService(repo, FakeProvider()).send_campaign(
        Campaign("Bulk"), [f"user{i}@example.com" for i in range(25)], "Hi", "Body", sender="sender@example.com"
    )
    elapsed = time.perf_counter() - start

    assert len(receipts) == 25
    assert elapsed < 2.0


@pytest.mark.performance
def test_queue_ledger_throughput_lightweight_benchmark():
    repo = LedgerRepository(":memory:")
    campaign = repo.create_campaign("Throughput")
    start = time.perf_counter()
    for i in range(40):
        recipient = repo.add_recipient(campaign.id, f"user{i}@example.com")
        repo.create_message(Message(campaign.id, recipient.id, "Hi", "Body"))
    elapsed = time.perf_counter() - start

    assert repo.status_counts(table="messages")[Status.QUEUED] == 40
    assert elapsed < 2.0


@pytest.mark.performance
def test_logging_throughput_lightweight_benchmark():
    logger = LoggingService(LogRepository(":memory:"))
    start = time.perf_counter()
    for i in range(75):
        logger.info("CAMPAIGN", "bulk log", item=i)
    elapsed = time.perf_counter() - start

    assert logger.summary()["total"] == 75
    assert elapsed < 1.0


@pytest.mark.performance
def test_dns_validation_concurrency_lightweight_benchmark(domain_service, fake_resolver):
    domains = [domain_service.add_domain(f"d{i}.example.com") for i in range(5)]
    for domain in domains:
        for record in domain_service.required_records(domain):
            fake_resolver.records[record.host] = [record.value]

    start = time.perf_counter()
    verified = [domain_service.verify_domain(domain.id) for domain in domains]
    elapsed = time.perf_counter() - start

    assert all(domain.is_verified for domain in verified)
    assert elapsed < 2.0
