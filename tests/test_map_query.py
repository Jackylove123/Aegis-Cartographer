from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from aegis_cartographer.core import Platform, extract_ui_elements, observe_screen
from aegis_cartographer.core.query import (
    MapPathExecutor,
    MapQueryService,
    render_maestro_flow,
)
from aegis_cartographer.core.query.maestro import validate_maestro_commands
from aegis_cartographer.core.storage import (
    ActionResultType,
    ExplorationAction,
    MapId,
    MapStore,
    ProjectWorkspace,
)

APP_ID = "com.example.app"


class FakePathDriver:
    def __init__(self, screens: dict[str, dict[str, Any]], entry: str) -> None:
        self.screens = screens
        self.entry = entry
        self.current = entry
        self.executed: list[list[Mapping[str, Any] | str]] = []

    def restart_app(self, *, timeout: float = 30.0) -> dict[str, Any]:
        self.current = self.entry
        return {"success": True}

    def wait_until_stable(
        self,
        *,
        timeout: float = 10.0,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        return {"success": True, "hierarchy": self.screens[self.current]}

    def execute_flow(
        self,
        commands: list[Mapping[str, Any] | str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.executed.append(commands)
        for command in commands:
            selector = command.get("tapOn") if isinstance(command, Mapping) else None
            resource_id = selector.get("id") if isinstance(selector, Mapping) else None
            if resource_id == "com.example:id/a_to_b":
                self.current = "B"
                return {"success": True}
            if resource_id == "com.example:id/b_to_c":
                self.current = "C"
                return {"success": True}
        return {"success": False, "error": "flow did not navigate"}


def screen(
    title: str,
    button_id: str,
    button_text: str,
) -> dict[str, Any]:
    return {
        "package": APP_ID,
        "tree": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
            "children": [
                {
                    "class": "android.widget.TextView",
                    "resource-id": "com.example:id/page_title",
                    "text": title,
                },
                {
                    "class": "android.widget.Button",
                    "resource-id": button_id,
                    "text": button_text,
                    "clickable": True,
                    "bounds": "[10,100][300,160]",
                },
            ],
        },
    }


def add_state(
    store: MapStore,
    payload: dict[str, Any],
    *,
    screen_name: str,
    element_name: str,
) -> tuple[Any, Any]:
    observation = observe_screen(payload, target_app_id=APP_ID)
    state = store.record_screen(observation, semantic_name=screen_name)
    element = store.record_element(
        state,
        extract_ui_elements(observation.hierarchy)[0],
        semantic_name=element_name,
    )
    return state, element


@pytest.fixture()
def populated_map(tmp_path: Path) -> tuple[ProjectWorkspace, MapId, MapStore]:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "10.0.0", "100", "zh-CN")
    store = MapStore.create(workspace, map_id)

    screen_a = screen("首页", "com.example:id/a_to_b", "进入订单")
    screen_b = screen("订单列表", "com.example:id/b_to_c", "进入详情")
    screen_c = screen("订单详情", "com.example:id/c_action", "修改收货地址")
    state_a, element_a = add_state(
        store,
        screen_a,
        screen_name="首页",
        element_name="进入订单",
    )
    state_b, element_b = add_state(
        store,
        screen_b,
        screen_name="订单列表",
        element_name="进入详情",
    )
    state_c, element_c = add_state(
        store,
        screen_c,
        screen_name="订单详情",
        element_name="修改收货地址",
    )
    store.set_entry_state(state_a.state_id)
    store.record_transition(
        from_state_id=state_a.state_id,
        element_id=element_a.element_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.NEW_PAGE,
        to_state_id=state_b.state_id,
        maestro_commands=({"tapOn": {"id": element_a.resource_id}},),
    )
    store.record_transition(
        from_state_id=state_b.state_id,
        element_id=element_b.element_id,
        action=ExplorationAction.TAP,
        result_type=ActionResultType.NEW_PAGE,
        to_state_id=state_c.state_id,
        maestro_commands=({"tapOn": {"id": element_b.resource_id}},),
    )

    _ = (screen_a, screen_b, screen_c)
    yield workspace, map_id, store
    store.close()


def test_element_query_returns_screen_locators_and_path(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    service = MapQueryService(store)

    results = service.locate_elements("修改收货地址")

    assert results
    top = results[0]
    assert top.element.resource_id == "com.example:id/c_action"
    assert top.screen is not None
    assert top.screen.semantic_name == "订单详情"
    assert top.path is not None
    assert len(top.path.steps) == 2
    assert [step.element_id for step in top.path.steps] == [
        next(
            element.element_id
            for element in store.list_elements()
            if element.resource_id == "com.example:id/a_to_b"
        ),
        next(
            element.element_id
            for element in store.list_elements()
            if element.resource_id == "com.example:id/b_to_c"
        ),
    ]
    assert top.path.reliability == 1.0
    assert [locator.strategy for locator in top.locators][0] == "id"


def test_element_path_uses_only_replayable_transition_commands(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    service = MapQueryService(store)
    target = next(
        element
        for element in store.list_elements()
        if element.resource_id == "com.example:id/c_action"
    )

    path = service.find_element_path(target.element_id)
    assert path is not None
    flow = service.generate_maestro_flow(path, include_target_tap=True)

    assert flow.startswith('{"appId":"com.example.app"}\n---\n')
    assert '{"tapOn":{"id":"com.example:id/a_to_b"}}' in flow
    assert '{"tapOn":{"id":"com.example:id/b_to_c"}}' in flow
    assert '{"tapOn":{"id":"com.example:id/c_action"}}' in flow


def test_screen_query_and_path(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    service = MapQueryService(store)
    target_element = next(
        element
        for element in store.list_elements()
        if element.resource_id == "com.example:id/c_action"
    )
    target_screen = target_element.screen_id

    results = service.locate_screens("订单详情")
    path = service.find_screen_path(target_screen)

    assert results
    assert results[0].screen.semantic_name == "订单详情"
    assert path is not None
    assert len(path.steps) == 2
    assert path.target_type == "screen"


def test_unreachable_path_is_reported_without_error(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    observation = observe_screen(
        screen("孤立页面", "com.example:id/orphan", "孤立按钮"),
        target_app_id=APP_ID,
    )
    state = store.record_screen(observation, semantic_name="孤立页面")
    element = store.record_element(
        state,
        extract_ui_elements(observation.hierarchy)[0],
    )
    service = MapQueryService(store)

    assert service.find_element_path(element.element_id) is None


def test_start_state_can_be_overridden(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    service = MapQueryService(store)
    target = next(
        element
        for element in store.list_elements()
        if element.resource_id == "com.example:id/c_action"
    )
    middle_state = next(
        state
        for state in store.list_screen_states()
        if state.state_id
        == next(
            element.state_id
            for element in store.list_elements()
            if element.resource_id == "com.example:id/b_to_c"
        )
    )

    path = service.find_element_path(
        target.element_id,
        start_state_id=middle_state.state_id,
    )

    assert path is not None
    assert path.start_state_id == middle_state.state_id
    assert len(path.steps) == 1


def test_flow_renderer_rejects_unknown_or_unsafe_commands() -> None:
    with pytest.raises(ValueError, match="Unsupported Maestro command"):
        render_maestro_flow([{"evilCommand": {"id": "x"}}])
    with pytest.raises(ValueError, match="exactly one key"):
        validate_maestro_commands([{"tapOn": {}, "pressKey": "BACK"}])
    with pytest.raises(ValueError, match="not safely serializable"):
        render_maestro_flow([{"tapOn": {"id": float("nan")}}])


def test_locator_and_transition_verification_results_are_recorded(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    transition = store.list_transitions()[0]
    element = store.get_element(transition.element_id)
    assert element is not None
    locator = min(
        element.locators,
        key=lambda item: (item.priority, item.value),
    )
    assert locator.locator_id is not None

    updated_element = store.record_locator_result(
        locator.locator_id,
        success=True,
    )
    updated_transition = store.record_transition_result(
        transition.transition_id,
        success=False,
    )

    assert updated_element is not None
    assert updated_element.last_verified_at is not None
    assert updated_transition is not None
    assert updated_transition.observed_count == 2
    assert updated_transition.success_count == 1


def test_path_executor_replays_route_and_verifies_target_locators(
    populated_map: tuple[ProjectWorkspace, MapId, MapStore],
) -> None:
    _workspace, _map_id, store = populated_map
    service = MapQueryService(store)
    target = next(
        element
        for element in store.list_elements()
        if element.resource_id == "com.example:id/c_action"
    )
    path = service.find_element_path(target.element_id)
    assert path is not None
    driver = FakePathDriver(
        {
            "A": screen("首页", "com.example:id/a_to_b", "进入订单"),
            "B": screen("订单列表", "com.example:id/b_to_c", "进入详情"),
            "C": screen("订单详情", "com.example:id/c_action", "修改收货地址"),
        },
        "A",
    )
    executor = MapPathExecutor(store, driver)
    before = {
        transition.transition_id: transition.observed_count
        for transition in store.list_transitions()
    }

    result = executor.execute(path)

    assert result.completed is True
    assert result.error is None
    assert result.target_element_found is True
    assert result.reached_state_id == path.target_state_id
    assert len(driver.executed) == 2
    assert all(
        store.get_transition(transition_id).observed_count == count + 1
        for transition_id, count in before.items()
    )
