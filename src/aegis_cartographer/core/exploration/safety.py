"""Safety classification for exploration tasks."""

from __future__ import annotations

from aegis_cartographer.core.exploration.models import (
    ExplorationTask,
    ExplorationTaskStatus,
    RiskLevel,
    SafetyPolicy,
)
from aegis_cartographer.core.storage.models import ElementRecord, ExplorationAction


class SafetyClassifier:
    """Classify tasks before touching a real device."""

    def __init__(self, policy: SafetyPolicy | None = None) -> None:
        self.policy = policy or SafetyPolicy()

    def classify(
        self,
        task: ExplorationTask,
        element: ElementRecord,
    ) -> tuple[RiskLevel, ExplorationTaskStatus | None, str | None]:
        """Return risk, optional non-executable status, and reason."""

        searchable = " ".join(
            [
                element.semantic_name or "",
                " ".join(element.aliases),
                element.text,
                element.content_desc,
                element.accessibility_id,
                element.resource_id,
                element.selector,
            ]
        ).casefold()

        for keyword in self.policy.blocked_keywords:
            if keyword.casefold() in searchable:
                return (
                    RiskLevel.HIGH,
                    ExplorationTaskStatus.BLOCKED,
                    f"blocked keyword: {keyword}",
                )
        for keyword in self.policy.needs_human_keywords:
            if keyword.casefold() in searchable:
                return (
                    RiskLevel.MEDIUM,
                    ExplorationTaskStatus.NEEDS_HUMAN,
                    f"needs human keyword: {keyword}",
                )

        if task.action == ExplorationAction.INPUT_TEXT and not self.policy.allow_input_text:
            return (
                RiskLevel.MEDIUM,
                ExplorationTaskStatus.NEEDS_HUMAN,
                "text input is disabled by policy",
            )

        risk = RiskLevel.HIGH if element.risk_level.casefold() == "high" else RiskLevel.LOW
        return risk, None, None
