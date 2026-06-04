"""Deliverability score engine combining domain, content, and ledger signals."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.models import DeliverabilityScore, EventType, LogComponent, ScoreComponent, Status
from backend.repositories import LedgerRepository
from backend.services.domain import DomainService
from backend.validators.compose import analyze_compose

_DOMAIN_WEIGHT = 25
_HISTORY_WEIGHT = 20
_SPAM_WEIGHT = 15
_CONTENT_WEIGHT = 25
_ENGAGEMENT_WEIGHT = 15
_WEIGHTS = {
    "domain": _DOMAIN_WEIGHT,
    "history": _HISTORY_WEIGHT,
    "spam": _SPAM_WEIGHT,
    "content": _CONTENT_WEIGHT,
    "engagement": _ENGAGEMENT_WEIGHT,
}


def _clamp(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def _sender_domain(sender: str | None) -> str | None:
    if not sender or "@" not in sender:
        return None
    return sender.split("@", 1)[1].strip().lower() or None


@dataclass(slots=True)
class _ComponentResult:
    """Internal score component result with advisory side effects."""

    component: ScoreComponent
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class DeliverabilityService:
    """Scores campaigns before delivery using domain, ledger, and compose signals."""

    def __init__(self, ledger: LedgerRepository, domains: DomainService | None = None, threshold: int = 70, logger: Any | None = None) -> None:
        self.ledger = ledger
        self.domains = domains
        self.threshold = _clamp(threshold)
        self.logger = logger

    def score_campaign(
        self,
        campaign_id: int,
        *,
        content: str | None = None,
        html: bool = False,
        sender: str | None = None,
    ) -> DeliverabilityScore:
        """Score a persisted campaign, using stored message content when no content is supplied."""
        campaign = self.ledger.get_campaign(campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} not found")
        messages = self.ledger.list_messages_for_campaign(campaign_id)
        scored_content = content
        if scored_content is None and messages:
            scored_content = messages[-1].content
        score = self._build_score(
            campaign_id=campaign_id,
            content=scored_content,
            html=html,
            sender=sender,
            recipients=None,
            use_history=True,
        )
        self._log_score(score, "campaign deliverability scored", campaign_id=campaign_id, sender=sender, html=html)
        return score

    def predict(
        self,
        content: str,
        recipients: Iterable[str],
        *,
        sender: str | None = None,
        html: bool = False,
    ) -> DeliverabilityScore:
        """Score prospective content and recipients without requiring persisted history."""
        recipient_list = list(recipients)
        score = self._build_score(
            campaign_id=None,
            content=content,
            html=html,
            sender=sender,
            recipients=recipient_list,
            use_history=False,
        )
        self._log_score(score, "prospective deliverability scored", sender=sender, html=html, recipients=len(recipient_list))
        return score

    def domain_reputation(self, sender: str | None) -> _ComponentResult:
        """Score sender-domain authentication health from the domain service."""
        domain_name = _sender_domain(sender)
        if domain_name is None:
            return _ComponentResult(
                ScoreComponent("domain", 70, _DOMAIN_WEIGHT, "No sender domain available; using neutral baseline."),
                warnings=["Sender domain was not provided for reputation scoring."],
                suggestions=["Use a verified sender domain before production sends."],
            )
        if self.domains is None:
            return _ComponentResult(
                ScoreComponent("domain", 70, _DOMAIN_WEIGHT, "Domain service unavailable; using neutral baseline."),
                warnings=["Domain verification data is unavailable."],
                suggestions=["Configure DomainService to include DKIM/SPF/DMARC reputation in scoring."],
            )
        domain = self.domains.get_domain_by_name(domain_name)
        if domain is None:
            return _ComponentResult(
                ScoreComponent("domain", 70, _DOMAIN_WEIGHT, f"{domain_name} is not managed; using penalized neutral score."),
                warnings=[f"Sender domain '{domain_name}' is not managed in Domain Manager."],
                suggestions=[f"Onboard and verify {domain_name} to improve deliverability."],
            )
        score = self.domains.health_score(domain)
        warnings: list[str] = []
        suggestions: list[str] = []
        if not domain.dkim_verified:
            warnings.append("DKIM is not verified for the sender domain.")
            suggestions.append("Publish and verify the DKIM TXT record.")
        if not domain.spf_verified:
            warnings.append("SPF is not verified for the sender domain.")
            suggestions.append("Publish and verify the SPF TXT record.")
        if not domain.dmarc_verified:
            warnings.append("DMARC is not verified for the sender domain.")
            suggestions.append("Publish and verify the DMARC TXT record.")
        return _ComponentResult(
            ScoreComponent("domain", _clamp(score), _DOMAIN_WEIGHT, f"Domain health for {domain_name}: {_clamp(score)}/100."),
            warnings=warnings,
            suggestions=suggestions,
        )

    def historical_inbox_placement(self, campaign_id: int | None) -> _ComponentResult:
        """Score delivered/sent placement against failed or bounced outcomes."""
        if campaign_id is None:
            return _ComponentResult(ScoreComponent("history", 75, _HISTORY_WEIGHT, "No persisted history for prediction."))
        rollups = self.ledger.recipient_status_rollups(campaign_id)
        positive = sum(rollups.get(status, 0) for status in (Status.SENT, Status.DELIVERED, Status.OPENED, Status.CLICKED))
        negative = sum(rollups.get(status, 0) for status in (Status.BOUNCED, Status.FAILED))
        total = positive + negative
        if total == 0:
            return _ComponentResult(ScoreComponent("history", 75, _HISTORY_WEIGHT, "No delivery history yet; using neutral baseline."))
        score = _clamp((positive / total) * 100)
        warnings = ["Historical bounces or failures reduce inbox placement."] if negative else []
        suggestions = ["Review failed recipients and suppress bad addresses before retrying."] if negative else []
        return _ComponentResult(
            ScoreComponent("history", score, _HISTORY_WEIGHT, f"{positive} positive vs {negative} failed/bounced outcomes."),
            warnings=warnings,
            suggestions=suggestions,
        )

    def spam_complaints(self, campaign_id: int | None) -> _ComponentResult:
        """Score bounce/failure/unsubscribe proxy signals as spam-risk complaints."""
        if campaign_id is None:
            return _ComponentResult(ScoreComponent("spam", 85, _SPAM_WEIGHT, "No complaint history for prediction."))
        messages = self.ledger.list_messages_for_campaign(campaign_id)
        if not messages:
            return _ComponentResult(ScoreComponent("spam", 85, _SPAM_WEIGHT, "No complaint history yet; using neutral baseline."))
        proxy_count = 0
        for message in messages:
            if message.id is None:
                continue
            for event in self.ledger.list_events_for_message(message.id):
                if event.status in {Status.BOUNCED, Status.FAILED, Status.UNSUBSCRIBED} or event.event_type in {
                    EventType.BOUNCE,
                    EventType.UNSUBSCRIBE,
                }:
                    proxy_count += 1
        rate = proxy_count / max(1, len(messages))
        score = _clamp(100 - (rate * 100))
        warnings = ["Bounce, failure, or unsubscribe signals are present."] if proxy_count else []
        suggestions = ["Suppress complainers and validate the list before the next send."] if proxy_count else []
        return _ComponentResult(
            ScoreComponent("spam", score, _SPAM_WEIGHT, f"{proxy_count} proxy complaint signals across {len(messages)} messages."),
            warnings=warnings,
            suggestions=suggestions,
        )

    def content_quality(self, content: str | None, *, html: bool = False) -> _ComponentResult:
        """Score message content using compose analysis without penalizing known personalization placeholders."""
        if content is None:
            return _ComponentResult(
                ScoreComponent("content", 75, _CONTENT_WEIGHT, "No content supplied; using neutral baseline."),
                warnings=["Content was not available for analysis."],
                suggestions=["Run Predict Before Send with the exact subject and content."],
            )
        analysis = analyze_compose(content, html=html)
        score = 100
        score -= min(70, int(analysis["spam_score"]) * 15)
        score -= min(20, len(analysis["unknown_placeholders"]) * 10)
        score -= min(20, len(analysis["jinja_errors"]) * 20)
        if html and float(analysis["html_text_ratio"]) < 0.10:
            score -= 15
        if html and any("unsubscribe" in warning.lower() for warning in analysis["warnings"]):
            score -= 5
        warnings = list(analysis["warnings"])
        suggestions: list[str] = []
        if analysis["spam_score"]:
            suggestions.append("Remove spam-trigger terms or rewrite the offer with clearer context.")
        if analysis["unknown_placeholders"]:
            suggestions.append("Replace unknown placeholders with supported autograb fields.")
        if analysis["jinja_errors"]:
            suggestions.append("Fix Jinja syntax before sending.")
        if html and float(analysis["html_text_ratio"]) < 0.10:
            suggestions.append("Add visible text to balance HTML markup.")
        detail = f"Spam words: {analysis['spam_score']}; text ratio: {analysis['html_text_ratio']}."
        return _ComponentResult(ScoreComponent("content", _clamp(score), _CONTENT_WEIGHT, detail), warnings, suggestions)

    def engagement(self, campaign_id: int | None) -> _ComponentResult:
        """Score optional open/click engagement when tracking events exist."""
        if campaign_id is None:
            return _ComponentResult(ScoreComponent("engagement", 75, _ENGAGEMENT_WEIGHT, "No engagement history for prediction."))
        messages = self.ledger.list_messages_for_campaign(campaign_id)
        sent_like = [message for message in messages if message.status in {Status.SENT, Status.DELIVERED, Status.OPENED, Status.CLICKED}]
        if not sent_like:
            return _ComponentResult(ScoreComponent("engagement", 75, _ENGAGEMENT_WEIGHT, "No open/click tracking yet; using neutral baseline."))
        engaged_ids: set[int] = set()
        for message in sent_like:
            if message.id is None:
                continue
            for event in self.ledger.list_events_for_message(message.id):
                if event.event_type in {EventType.OPEN, EventType.CLICK} or event.status in {Status.OPENED, Status.CLICKED}:
                    engaged_ids.add(message.id)
        if not engaged_ids:
            return _ComponentResult(ScoreComponent("engagement", 75, _ENGAGEMENT_WEIGHT, "No open/click events recorded; neutral baseline."))
        rate = len(engaged_ids) / max(1, len(sent_like))
        score = _clamp(50 + rate * 50)
        return _ComponentResult(
            ScoreComponent("engagement", score, _ENGAGEMENT_WEIGHT, f"{len(engaged_ids)} engaged messages across {len(sent_like)} sent messages.")
        )

    def _build_score(
        self,
        *,
        campaign_id: int | None,
        content: str | None,
        html: bool,
        sender: str | None,
        recipients: list[str] | None,
        use_history: bool,
    ) -> DeliverabilityScore:
        results = [
            self.domain_reputation(sender),
            self.historical_inbox_placement(campaign_id if use_history else None),
            self.spam_complaints(campaign_id if use_history else None),
            self.content_quality(content, html=html),
            self.engagement(campaign_id if use_history else None),
        ]
        recipient_warnings, recipient_suggestions = self._recipient_signals(recipients)
        weighted = sum(result.component.score * result.component.weight for result in results) / sum(_WEIGHTS.values())
        score = _clamp(weighted)
        warnings: list[str] = []
        suggestions: list[str] = []
        for result in results:
            warnings.extend(result.warnings)
            suggestions.extend(result.suggestions)
        warnings.extend(recipient_warnings)
        suggestions.extend(recipient_suggestions)
        passed = score >= self.threshold
        if not passed:
            suggestions.append(f"Raise the score to at least {self.threshold} before sending.")
        return DeliverabilityScore(
            score=score,
            components=[result.component for result in results],
            warnings=list(dict.fromkeys(warnings)),
            suggestions=list(dict.fromkeys(suggestions)),
            threshold=self.threshold,
            passed=passed,
        )

    def _log_score(self, score: DeliverabilityScore, message: str, **context: Any) -> None:
        if self.logger is not None:
            self.logger.log(
                LogComponent.DELIVERABILITY,
                "INFO" if score.passed else "WARNING",
                message,
                score=score.score,
                threshold=score.threshold,
                passed=score.passed,
                warnings=score.warnings,
                suggestions=score.suggestions,
                **context,
            )

    def _recipient_signals(self, recipients: list[str] | None) -> tuple[list[str], list[str]]:
        if recipients is None:
            return [], []
        invalid = [recipient for recipient in recipients if "@" not in recipient]
        duplicates = len(recipients) - len(set(recipients))
        warnings: list[str] = []
        suggestions: list[str] = []
        if invalid:
            warnings.append(f"{len(invalid)} recipient addresses are malformed.")
            suggestions.append("Remove malformed recipient addresses before sending.")
        if duplicates:
            warnings.append(f"{duplicates} duplicate recipient addresses detected.")
            suggestions.append("Deduplicate recipients to reduce complaint risk.")
        return warnings, suggestions
