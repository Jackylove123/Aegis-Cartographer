"""Replay and verify planned paths against a device driver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from aegis_cartographer.core import extract_ui_elements, observe_screen
from aegis_cartographer.core.identity import derive_state_id
from aegis_cartographer.core.query.models import PathExecutionResult, PathPlan
from aegis_cartographer.core.storage.models import ElementLocator, ElementRecord
from aegis_cartographer.core.storage.store import MapStore


class PathDriverProtocol(Protocol):
    """Device operations required to replay a map-derived route."""

    def restart_app(self, *, timeout: float = 30.0) -> dict[str, Any]:
        """Restart the target app."""

    def execute_flow(
        self,
        commands: Sequence[Mapping[str, Any] | str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a generated Maestro flow."""

    def wait_until_stable(
        self,
        *,
        timeout: float = 10.0,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        """Wait for a stable hierarchy."""


class MapPathExecutor:
    """Replay routes and write verification results back to the map."""

    def __init__(self, store: MapStore, driver: PathDriverProtocol) -> None:
        if store.readonly:
            raise ValueError("Path verification requires a writable MapStore")
        self.store = store
        self.driver = driver

    def execute(
        self,
        path: PathPlan,
        *,
        verify_target_element: bool = True,
    ) -> PathExecutionResult:
        """Restart at the map entry, replay a path, and verify its target."""

        restart = self.driver.restart_app()
        if not restart.get("success"):
            return self._failure(path, None, None, "app restart failed")

        observed_state = self._observe_state()
        if observed_state != path.start_state_id:
            return self._failure(
                path,
                observed_state,
                None,
                f"entry state mismatch: expected {path.start_state_id}, got {observed_state}",
            )

        executed: list[str] = []
        current_state = path.start_state_id
        for step in path.steps:
            result = self.driver.execute_flow(list(step.maestro_commands))
            success = bool(result.get("success"))
            self.store.record_transition_result(
                step.transition.transition_id,
                success=success,
            )
            if not success:
                return self._failure(
                    path,
                    current_state,
                    tuple(executed),
                    result.get("error") or "transition replay failed",
                    failed_transition_id=step.transition.transition_id,
                )
            observed_state = self._observe_state()
            if observed_state != step.to_state_id:
                return self._failure(
                    path,
                    observed_state,
                    tuple(executed),
                    (
                        f"transition landed on {observed_state}, "
                        f"expected {step.to_state_id}"
                    ),
                    failed_transition_id=step.transition.transition_id,
                )
            current_state = observed_state
            executed.append(step.transition.transition_id)

        target_found: bool | None = None
        if verify_target_element and path.target_type == "element":
            element = self.store.get_element(path.target_id)
            if element is None:
                return self._failure(
                    path,
                    current_state,
                    tuple(executed),
                    "target element is missing from the map",
                )
            target_found = self._verify_element_locators(element)

        return PathExecutionResult(
            path=path,
            completed=current_state == path.target_state_id,
            reached_state_id=current_state,
            executed_transition_ids=tuple(executed),
            failed_transition_id=None,
            target_element_found=target_found,
            error=None,
        )

    def _observe_state(self) -> str | None:
        stable = self.driver.wait_until_stable()
        hierarchy = stable.get("hierarchy")
        if not isinstance(hierarchy, dict) or not hierarchy:
            return None
        observation = observe_screen(
            hierarchy,
            target_app_id=self.store.map_id.app_id,
        )
        return derive_state_id(observation)

    def _verify_element_locators(self, element: ElementRecord) -> bool:
        stable = self.driver.wait_until_stable()
        hierarchy = stable.get("hierarchy")
        if not isinstance(hierarchy, dict) or not hierarchy:
            return False
        observed = extract_ui_elements(hierarchy, include_non_actionable=True)
        any_success = False
        for locator in element.locators:
            success = self._locator_matches(locator, element, observed)
            any_success = any_success or success
            if locator.locator_id is not None:
                self.store.record_locator_result(
                    locator.locator_id,
                    success=success,
                    error=None if success else "locator not found in target hierarchy",
                )
        return any_success

    @staticmethod
    def _locator_matches(
        locator: ElementLocator,
        element: ElementRecord,
        observed: Sequence[Any],
    ) -> bool:
        if locator.strategy == "id":
            candidates = [item for item in observed if item.resource_id == locator.value]
        elif locator.strategy == "accessibility_id":
            candidates = [
                item for item in observed if item.accessibility_id == locator.value
            ]
        elif locator.strategy == "label":
            candidates = [item for item in observed if item.content_desc == locator.value]
        elif locator.strategy == "text":
            candidates = [item for item in observed if item.text == locator.value]
        elif locator.strategy == "point":
            try:
                x, y = (int(value) for value in locator.value.split(","))
            except ValueError:
                return False
            candidates = [
                item
                for item in observed
                if item.bounds.center_x == x and item.bounds.center_y == y
            ]
        else:
            return False
        return len(candidates) >= element.occurrence_index

    @staticmethod
    def _failure(
        path: PathPlan,
        reached_state_id: str | None,
        executed_transition_ids: tuple[str, ...],
        error: str,
        *,
        failed_transition_id: str | None = None,
    ) -> PathExecutionResult:
        return PathExecutionResult(
            path=path,
            completed=False,
            reached_state_id=reached_state_id,
            executed_transition_ids=executed_transition_ids,
            failed_transition_id=failed_transition_id,
            target_element_found=None,
            error=error,
        )
