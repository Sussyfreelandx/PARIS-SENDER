"""Deliverability score models for API and UI reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScoreComponent:
    """One weighted component in the deliverability score breakdown."""

    name: str
    score: int
    weight: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the component to a JSON-friendly dict."""
        return {
            "name": self.name,
            "score": self.score,
            "weight": self.weight,
            "detail": self.detail,
        }


@dataclass(slots=True)
class DeliverabilityScore:
    """Overall deliverability score with weighted component breakdown."""

    score: int
    components: list[ScoreComponent]
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    threshold: int = 70
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize the score to a JSON-friendly dict for the API/UI."""
        return {
            "score": self.score,
            "components": [component.to_dict() for component in self.components],
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "threshold": self.threshold,
            "passed": self.passed,
        }
