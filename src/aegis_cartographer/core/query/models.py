"""Typed results returned by the map query service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aegis_cartographer.core.storage.models import (
    ElementLocator,
    ElementRecord,
    ScreenRecord,
    TransitionRecord,
)


@dataclass(frozen=True)
class PathStep:
    """One replayable graph edge in a planned route."""

    transition: TransitionRecord
    from_state_id: str
    to_state_id: str
    element_id: str | None
    action: str
    result_type: str
    maestro_commands: tuple[Mapping[str, Any], ...]
    restore_strategy: tuple[Mapping[str, Any], ...]
    reliability: float


@dataclass(frozen=True)
class PathPlan:
    """A route from a start state to a target state."""

    start_state_id: str
    target_state_id: str
    target_type: str
    target_id: str
    steps: tuple[PathStep, ...]
    total_cost: float
    reliability: float

    @property
    def is_reachable(self) -> bool:
        return True

    def maestro_commands(self) -> tuple[Mapping[str, Any], ...]:
        """Return navigation commands in execution order."""

        commands: list[Mapping[str, Any]] = []
        for step in self.steps:
            commands.extend(step.maestro_commands)
        return tuple(commands)


@dataclass(frozen=True)
class ElementQueryResult:
    """A query hit with element locators and an optional route."""

    query: str
    score: float
    matched_source: str
    element: ElementRecord
    screen: ScreenRecord | None
    locators: tuple[ElementLocator, ...]
    path: PathPlan | None


@dataclass(frozen=True)
class ScreenQueryResult:
    """A query hit for a logical screen and an optional route."""

    query: str
    score: float
    matched_source: str
    screen: ScreenRecord
    state_ids: tuple[str, ...]
    path: PathPlan | None


@dataclass(frozen=True)
class PathExecutionResult:
    """Result of replaying a planned path on a real or fake device."""

    path: PathPlan
    completed: bool
    reached_state_id: str | None
    executed_transition_ids: tuple[str, ...]
    failed_transition_id: str | None
    target_element_found: bool | None
    error: str | None
