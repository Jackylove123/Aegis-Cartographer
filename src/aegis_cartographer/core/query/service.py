"""High-level query and path planning over one project-local map."""

from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from typing import Any

from aegis_cartographer.core.query.maestro import render_maestro_flow
from aegis_cartographer.core.query.models import (
    ElementQueryResult,
    PathPlan,
    PathStep,
    ScreenQueryResult,
)
from aegis_cartographer.core.storage.models import (
    ActionResultType,
    ElementLocator,
    ElementRecord,
    TransitionRecord,
)
from aegis_cartographer.core.storage.store import MapStore

_NAVIGATION_RESULTS = {
    ActionResultType.NEW_PAGE,
    ActionResultType.OVERLAY,
    ActionResultType.STATE_CHANGE,
}


class MapQueryService:
    """Read-only semantic and graph queries for one map artifact."""

    def __init__(self, store: MapStore) -> None:
        self.store = store

    def locate_elements(
        self,
        query: str,
        *,
        limit: int = 10,
        start_state_id: str | None = None,
    ) -> list[ElementQueryResult]:
        """Search elements and attach a route whenever one is reachable."""

        hits = self.store.search_elements(query, limit=limit)
        results: list[ElementQueryResult] = []
        for hit in hits:
            path = self.find_element_path(
                hit.element.element_id,
                start_state_id=start_state_id,
            )
            results.append(
                ElementQueryResult(
                    query=query,
                    score=hit.score,
                    matched_source=hit.matched_source,
                    element=hit.element,
                    screen=self.store.get_screen(hit.element.screen_id),
                    locators=tuple(
                        sorted(hit.element.locators, key=lambda item: item.priority)
                    ),
                    path=path,
                )
            )
        return results

    def locate_screens(
        self,
        query: str,
        *,
        limit: int = 10,
        start_state_id: str | None = None,
    ) -> list[ScreenQueryResult]:
        """Search logical screens and attach a route to the best state."""

        hits = self.store.search_screens(query, limit=limit)
        results: list[ScreenQueryResult] = []
        for hit in hits:
            screen = self.store.get_screen(hit.screen_id)
            if screen is None:
                continue
            path = self.find_screen_path(
                hit.screen_id,
                start_state_id=start_state_id,
            )
            results.append(
                ScreenQueryResult(
                    query=query,
                    score=hit.score,
                    matched_source=hit.matched_source,
                    screen=screen,
                    state_ids=hit.state_ids,
                    path=path,
                )
            )
        return results

    def find_element_path(
        self,
        element_id: str,
        *,
        start_state_id: str | None = None,
    ) -> PathPlan | None:
        """Find a reliable route to the state containing an element."""

        element = self.store.get_element(element_id)
        if element is None:
            raise ValueError(f"Unknown element: {element_id}")
        return self._plan(
            target_type="element",
            target_id=element.element_id,
            target_state_ids=(element.state_id,),
            start_state_id=start_state_id,
        )

    def find_screen_path(
        self,
        screen_id: str,
        *,
        start_state_id: str | None = None,
    ) -> PathPlan | None:
        """Find a reliable route to any state of a logical screen."""

        states = self.store.list_screen_states(screen_id=screen_id)
        if not states:
            raise ValueError(f"Unknown screen: {screen_id}")
        return self._plan(
            target_type="screen",
            target_id=screen_id,
            target_state_ids=tuple(state.state_id for state in states),
            start_state_id=start_state_id,
        )

    def find_state_path(
        self,
        start_state_id: str,
        target_state_id: str,
    ) -> PathPlan | None:
        """Find a reliable route between two explicitly known page states."""

        if self.store.get_screen_state(start_state_id) is None:
            raise ValueError(f"Unknown start screen state: {start_state_id}")
        if self.store.get_screen_state(target_state_id) is None:
            raise ValueError(f"Unknown target screen state: {target_state_id}")
        return self._plan(
            target_type="screen_state",
            target_id=target_state_id,
            target_state_ids=(target_state_id,),
            start_state_id=start_state_id,
        )

    def generate_maestro_flow(
        self,
        path: PathPlan,
        *,
        include_target_tap: bool = False,
    ) -> str:
        """Render a planned route as a safe Maestro YAML flow."""

        commands: list[Mapping[str, Any] | str] = list(path.maestro_commands())
        if include_target_tap:
            if path.target_type != "element":
                raise ValueError("Only an element path can include a target tap")
            element = self.store.get_element(path.target_id)
            if element is None:
                raise ValueError("Target element disappeared from the map")
            target_command = self._target_tap_command(element)
            if target_command is not None:
                commands.append(target_command)
        return render_maestro_flow(
            commands,
            app_id=self.store.map_id.app_id,
        )

    @staticmethod
    def _target_tap_command(element: ElementRecord) -> Mapping[str, Any] | None:
        if not element.locators:
            return None
        locator = min(element.locators, key=lambda item: (item.priority, item.value))
        selector = MapQueryService._locator_selector(locator, element)
        return None if selector is None else {"tapOn": selector}

    @staticmethod
    def _locator_selector(
        locator: ElementLocator,
        element: ElementRecord,
    ) -> dict[str, Any] | None:
        selector: dict[str, Any] | None = None
        if locator.strategy == "id":
            selector = {"id": locator.value}
        elif locator.strategy in {"accessibility_id", "label"}:
            selector = {"label": locator.value}
        elif locator.strategy == "text":
            selector = {"text": locator.value}
        elif locator.strategy == "point":
            selector = {"point": locator.value}
        if selector is not None and element.occurrence_index > 1:
            selector["index"] = element.occurrence_index - 1
        return selector

    def _resolve_start_state(self, start_state_id: str | None) -> str:
        if start_state_id is not None:
            if self.store.get_screen_state(start_state_id) is None:
                raise ValueError(f"Unknown start screen state: {start_state_id}")
            return start_state_id
        entry = self.store.get_entry_state()
        if entry is None:
            raise ValueError("Map has no entry state and no start_state_id was provided")
        return entry.state_id

    def _plan(
        self,
        *,
        target_type: str,
        target_id: str,
        target_state_ids: Sequence[str],
        start_state_id: str | None,
    ) -> PathPlan | None:
        start = self._resolve_start_state(start_state_id)
        targets = set(target_state_ids)
        if start in targets:
            return PathPlan(
                start_state_id=start,
                target_state_id=start,
                target_type=target_type,
                target_id=target_id,
                steps=(),
                total_cost=0.0,
                reliability=1.0,
            )

        transitions = self._usable_transitions()
        adjacency: dict[str, list[TransitionRecord]] = {}
        for transition in transitions:
            if transition.to_state_id is None:
                continue
            adjacency.setdefault(transition.from_state_id, []).append(transition)

        costs: dict[str, float] = {start: 0.0}
        previous: dict[str, tuple[str, TransitionRecord]] = {}
        queue: list[tuple[float, str, str]] = [(0.0, start, "")]
        visited: set[str] = set()

        while queue:
            cost, state_id, transition_id = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)
            if state_id in targets:
                break
            for transition in adjacency.get(state_id, ()):
                if transition.to_state_id is None or transition.to_state_id in visited:
                    continue
                reliability = self._reliability(transition)
                edge_cost = 1.0 + (2.0 * (1.0 - reliability))
                next_cost = cost + edge_cost
                destination = transition.to_state_id
                if next_cost < costs.get(destination, float("inf")):
                    costs[destination] = next_cost
                    previous[destination] = (state_id, transition)
                    heapq.heappush(
                        queue,
                        (
                            next_cost,
                            destination,
                            transition.transition_id,
                        ),
                    )

        reachable_targets = [state for state in targets if state in costs]
        if not reachable_targets:
            return None
        target = min(reachable_targets, key=lambda state: (costs[state], state))
        steps_reversed: list[PathStep] = []
        cursor = target
        while cursor != start:
            edge = previous.get(cursor)
            if edge is None:
                raise RuntimeError("Path reconstruction found a missing predecessor")
            source, transition = edge
            steps_reversed.append(
                PathStep(
                    transition=transition,
                    from_state_id=source,
                    to_state_id=cursor,
                    element_id=transition.element_id,
                    action=transition.action.value,
                    result_type=transition.result_type.value,
                    maestro_commands=transition.maestro_commands,
                    restore_strategy=transition.restore_strategy,
                    reliability=self._reliability(transition),
                )
            )
            cursor = source
        steps = tuple(reversed(steps_reversed))
        reliability = 1.0
        for step in steps:
            reliability *= step.reliability
        return PathPlan(
            start_state_id=start,
            target_state_id=target,
            target_type=target_type,
            target_id=target_id,
            steps=steps,
            total_cost=costs[target],
            reliability=reliability,
        )

    def _usable_transitions(self) -> list[TransitionRecord]:
        usable: list[TransitionRecord] = []
        for transition in self.store.list_transitions():
            if transition.to_state_id is None:
                continue
            if transition.result_type not in _NAVIGATION_RESULTS:
                continue
            if transition.observed_count <= 0 or transition.success_count <= 0:
                continue
            if not transition.maestro_commands:
                continue
            if transition.element_id is not None:
                element = self.store.get_element(transition.element_id)
                if element is None:
                    continue
            usable.append(transition)
        return usable

    @staticmethod
    def _reliability(transition: TransitionRecord) -> float:
        if transition.observed_count <= 0:
            return 0.0
        return min(
            1.0,
            max(0.01, transition.success_count / transition.observed_count),
        )
