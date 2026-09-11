from __future__ import annotations

from typing import Any

import pytest

from aegis_cartographer.core import Platform, extract_ui_elements, observe_screen
from aegis_cartographer.core.exploration import (
    ExplorationBudget,
    ExplorationEngine,
    ExplorationStatus,
    ExplorationTask,
    ExplorationTaskStatus,
    RiskLevel,
    SafetyClassifier,
    SafetyPolicy,
)
from aegis_cartographer.core.query import MapQueryService
from aegis_cartographer.core.storage import MapId, MapStore, ProjectWorkspace
from aegis_cartographer.core.storage.models import ActionResultType, ExplorationAction
from tests.test_map_storage import populated_store

APP_ID = "com.example.app"


def screen_payload(
    title: str,
    buttons: list[tuple[str, str, int]],
    *,
    package: str = APP_ID,
) -> dict[str, Any]:
    return {
        "package": package,
        "tree": {
            "class": "android.widget.FrameLayout",
            "resource-id": "android:id/content",
            "children": [
                {
                    "class": "android.widget.TextView",
                    "resource-id": "com.example:id/page_title",
                    "text": title,
                },
                *[
                    {
                        "class": "android.widget.Button",
                        "resource-id": resource_id,
                        "text": text,
                        "clickable": True,
                        "bounds": f"[10,{top}[300,{top + 60}]",
                    }
                    for resource_id, text, top in buttons
                ],
            ],
        },
    }


SCREEN_A = screen_payload(
    "首页",
    [
        ("com.example:id/go_detail", "查看详情", 100),
        ("com.example:id/home_noop", "首页按钮", 200),
    ],
)
SCREEN_B = screen_payload(
    "详情页",
    [
        ("com.example:id/detail_noop", "详情按钮", 100),
    ],
)
SCROLLED_A = {
    "package": APP_ID,
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            {
                "class": "android.widget.TextView",
                "resource-id": "com.example:id/page_title",
                "text": "首页",
            },
            {
                "class": "android.widget.ScrollView",
                "resource-id": "com.example:id/scroll",
                "scrollable": True,
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.example:id/item_1",
                        "text": "第一项",
                        "clickable": True,
                    },
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.example:id/item_2",
                        "text": "第二项",
                        "clickable": True,
                    },
                ],
            },
        ],
    },
}
INITIAL_SCROLL = {
    "package": APP_ID,
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            SCROLLED_A["tree"]["children"][0],
            {
                "class": "android.widget.ScrollView",
                "resource-id": "com.example:id/scroll",
                "scrollable": True,
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.example:id/item_1",
                        "text": "第一项",
                        "clickable": True,
                    },
                ],
            },
        ],
    },
}
DIALOG_SCREEN = {
    "package": APP_ID,
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            {
                "class": "androidx.appcompat.widget.AppCompatAlertDialog",
                "resource-id": "com.example:id/dialog",
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.example:id/dialog_confirm",
                        "text": "确认",
                        "clickable": True,
                    },
                ],
            },
        ],
    },
}
EXTERNAL_SCREEN = screen_payload(
    "外部页面",
    [("com.example:id/external_noop", "外部按钮", 100)],
    package="com.android.chrome",
)


class FakeDriver:
    def __init__(
        self,
        *,
        fail_resource_id: str | None = None,
        initial_hierarchy: dict[str, Any] | None = None,
    ) -> None:
        self.current = initial_hierarchy or SCREEN_A
        self.fail_resource_id = fail_resource_id
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.entry_hierarchy = self.current

    def get_hierarchy(self) -> dict[str, Any]:
        return self.current

    def wait_until_stable(
        self,
        *,
        timeout: float = 10.0,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "stable": True,
            "hierarchy": self.current,
            "state_id": "fake",
        }

    def _should_fail(self, arguments: dict[str, Any]) -> bool:
        target = (
            arguments.get("element_id")
            or arguments.get("text")
            or arguments.get("label")
            or (",".join(str(value) for value in arguments.get("point") or ()))
        )
        if self.fail_resource_id is None:
            return False
        failing_texts = {
            node.get("text", "")
            for node in self.current.get("tree", {}).get("children", [])
            if isinstance(node, dict)
            and node.get("resource-id") == self.fail_resource_id
        }
        return target in {self.fail_resource_id, *failing_texts, "155,130"}

    def tap_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        arguments = {
            "element_id": element_id,
            "text": text,
            "bounds": bounds,
            "label": label,
            "point": point,
            "occurrence_index": occurrence_index,
        }
        self.actions.append(("tap", arguments))
        if self._should_fail(arguments):
            return {"success": False, "error": "element not found"}
        if element_id == "com.example:id/go_detail":
            self.current = SCREEN_B
        return {"success": True}

    def long_press_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        return self.tap_on(
            element_id,
            text,
            bounds,
            label=label,
            point=point,
            occurrence_index=occurrence_index,
            timeout=timeout,
        )

    def swipe(self, direction: str, *, timeout: float = 20.0) -> dict[str, Any]:
        self.actions.append(("swipe", {"direction": direction}))
        return {"success": True}

    def back(self, *, timeout: float = 15.0) -> dict[str, Any]:
        self.actions.append(("back", {}))
        self.current = self.entry_hierarchy
        return {"success": True}

    def restart_app(self, *, timeout: float = 30.0) -> dict[str, Any]:
        self.actions.append(("restart", {}))
        self.current = self.entry_hierarchy
        return {"success": True}


class ExternalDriver(FakeDriver):
    def tap_on(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().tap_on(*args, **kwargs)
        if kwargs.get("element_id") == "com.example:id/go_external":
            self.current = EXTERNAL_SCREEN
        return result


class ScrollingDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__(initial_hierarchy=INITIAL_SCROLL)

    def swipe(self, direction: str, *, timeout: float = 20.0) -> dict[str, Any]:
        result = super().swipe(direction, timeout=timeout)
        self.current = SCROLLED_A
        return result


class OverlayDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__(
            initial_hierarchy=screen_payload(
                "首页",
                [("com.example:id/open_dialog", "打开弹窗", 100)],
            ),
        )

    def tap_on(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().tap_on(*args, **kwargs)
        if kwargs.get("element_id") == "com.example:id/open_dialog":
            self.current = DIALOG_SCREEN
        return result


def test_dfs_creates_states_tasks_transitions_and_checkpoint(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "1", "zh-CN")
    driver = FakeDriver()

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-dfs",
            target_app_id=APP_ID,
            budget=ExplorationBudget(max_actions=30, max_errors=10),
        )
        checkpoint = engine.run()
        report = engine.coverage_report()

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert checkpoint.stack == []
        assert checkpoint.states_created == 2
        assert report["map_statistics"]["screen_states"] == 2
        assert report["map_statistics"]["transitions"] >= 3
        assert report["task_statuses"]["DONE"] == 6
        assert report["pending_tasks"] == 0
        page_statuses = {
            page.screen_id: page.coverage_status
            for page in store.list_page_summaries()
        }
        assert page_statuses
        assert all(status == "DEEP_DONE" for status in page_statuses.values())
        assert ("back", {}) in driver.actions
        assert (workspace.runs_dir / "run-dfs" / "tasks.sqlite").is_file()
        map_tables = {
            str(row["name"])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "exploration_tasks" not in map_tables
        commands = [
            str(row["maestro_commands_json"])
            for row in store.connection.execute(
                "SELECT maestro_commands_json FROM transitions"
            )
        ]
        assert any(
            "tapOn" in command and "com.example:id/go_detail" in command
            for command in commands
        )
        query_service = MapQueryService(store)
        detail_results = query_service.locate_elements("详情按钮")
        assert detail_results
        assert detail_results[0].path is not None
        assert detail_results[0].path.start_state_id == store.get_entry_state().state_id

    checkpoint_path = workspace.runs_dir / "run-dfs" / "checkpoint.json"
    assert checkpoint_path.is_file()


def test_exploration_can_resume_from_checkpoint(tmp_path: Any) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "2", "zh-CN")
    driver = FakeDriver()

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-resume",
            target_app_id=APP_ID,
        )
        first = engine.run(max_steps=1)
        assert first.status is ExplorationStatus.PAUSED
        assert first.actions_executed == 1

    with MapStore.open(workspace, map_id, readonly=False) as store:
        resumed_driver = FakeDriver()
        engine = ExplorationEngine(
            store=store,
            driver=resumed_driver,
            run_id="run-resume",
            target_app_id=APP_ID,
        )
        resumed = engine.run()
        report = engine.coverage_report()

        assert resumed.status is ExplorationStatus.COMPLETED
        assert report["task_statuses"]["DONE"] == 6
        assert report["pending_tasks"] == 0


def test_external_app_transition_is_recorded_and_engine_returns(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "3", "zh-CN")
    payload = screen_payload(
        "首页",
        [("com.example:id/go_external", "打开外部", 100)],
    )
    driver = ExternalDriver(initial_hierarchy=payload)

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-external",
            target_app_id=APP_ID,
        )
        checkpoint = engine.run()
        transitions = [
            tuple(row)
            for row in store.connection.execute(
                "SELECT result_type FROM transitions"
            ).fetchall()
        ]

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert store.statistics()["screen_states"] == 1
        assert (ActionResultType.EXTERNAL_APP.value,) in transitions
    assert driver.current == payload
    assert ("back", {}) in driver.actions


def test_foreground_package_prevents_blank_hierarchy_misregistration(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "20", "zh-CN")
    payload = screen_payload("外部页面", [], package="")

    class ForegroundExternalDriver(FakeDriver):
        def get_foreground_activity(self) -> Any:
            return type(
                "ForegroundInfo",
                (),
                {"success": True, "package_name": "com.android.external"},
            )()

    driver = ForegroundExternalDriver(initial_hierarchy=payload)
    with MapStore.create(workspace, map_id) as store:
        with pytest.raises(ValueError, match="outside the target app"):
            ExplorationEngine(
                store=store,
                driver=driver,
                run_id="run-foreground-external",
                target_app_id=APP_ID,
            )
        assert store.statistics()["screen_states"] == 0


def test_restart_can_recover_to_a_verified_one_time_root_successor(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "21", "zh-CN")

    class OneTimeRootDriver(FakeDriver):
        def back(self, *, timeout: float = 15.0) -> dict[str, Any]:
            self.actions.append(("back", {}))
            self.current = SCREEN_B
            return {"success": True}

        def restart_app(self, *, timeout: float = 30.0) -> dict[str, Any]:
            self.actions.append(("restart", {}))
            self.current = SCREEN_B
            return {"success": True}

    driver = OneTimeRootDriver()
    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-one-time-root",
            target_app_id=APP_ID,
        )
        checkpoint = engine.run()

        assert checkpoint.status is ExplorationStatus.NEEDS_HUMAN
        assert "start a continuation run" in (checkpoint.last_error or "")
        assert ("restart", {}) in driver.actions


def test_scroll_task_discovers_offscreen_elements(tmp_path: Any) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "7", "zh-CN")
    driver = ScrollingDriver()

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-scroll",
            target_app_id=APP_ID,
        )
        checkpoint = engine.run()
        element_ids = {
            element.resource_id
            for element in store.list_elements()
        }
        result_types = {
            str(row["result_type"])
            for row in store.connection.execute("SELECT result_type FROM transitions")
        }

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert "com.example:id/item_2" in element_ids
        assert ActionResultType.STATE_CHANGE.value in result_types
        assert driver.current == INITIAL_SCROLL


def test_overlay_is_explored_and_closed_before_returning_to_parent(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "8", "zh-CN")
    driver = OverlayDriver()

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-overlay",
            target_app_id=APP_ID,
        )
        checkpoint = engine.run()
        state_types = {
            str(row["state_type"])
            for row in store.connection.execute("SELECT state_type FROM screen_states")
        }
        result_types = {
            str(row["result_type"])
            for row in store.connection.execute("SELECT result_type FROM transitions")
        }

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert "dialog" in state_types
        assert ActionResultType.OVERLAY.value in result_types
        assert driver.current == DIALOG_SCREEN or ("back", {}) in driver.actions


def test_failed_action_is_retried_then_marked_failed(tmp_path: Any) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "4", "zh-CN")
    payload = screen_payload(
        "首页",
        [("com.example:id/broken", "不可见按钮", 100)],
    )
    driver = FakeDriver(
        fail_resource_id="com.example:id/broken",
        initial_hierarchy=payload,
    )

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-failure",
            target_app_id=APP_ID,
            budget=ExplorationBudget(max_actions=20, max_errors=10, max_retries=1),
        )
        checkpoint = engine.run()
        tasks = engine.task_store.list()

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert all(task.status is ExplorationTaskStatus.FAILED for task in tasks)
        assert all(task.attempt_count == 2 for task in tasks)
        assert len([action for action in driver.actions if action[0] == "tap"]) >= 4


def test_engine_records_blocked_task_without_touching_device(tmp_path: Any) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "9", "zh-CN")
    payload = screen_payload(
        "首页",
        [("com.example:id/delete", "删除订单", 100)],
    )
    driver = FakeDriver(initial_hierarchy=payload)

    with MapStore.create(workspace, map_id) as store:
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id="run-blocked",
            target_app_id=APP_ID,
        )
        checkpoint = engine.run()
        report = engine.coverage_report()

        assert checkpoint.status is ExplorationStatus.COMPLETED
        assert report["blocked_tasks"] == 2
        assert report["pending_tasks"] == 0
        assert driver.actions == []


def test_safety_classifier_blocks_dangerous_and_input_tasks(
    tmp_path: Any,
) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "5", "zh-CN")

    with populated_store(workspace, map_id) as store:
        element = store.list_elements()[0]
        dangerous_observation = observe_screen(
            screen_payload("首页", [("com.example:id/delete", "删除订单", 100)]),
            target_app_id=APP_ID,
        )
        dangerous_state = store.record_screen(dangerous_observation)
        dangerous = store.record_element(
            dangerous_state,
            extract_ui_elements(dangerous_observation.hierarchy)[0],
            semantic_name="删除订单",
        )
        classifier = SafetyClassifier(SafetyPolicy())

        normal_task = ExplorationTask(
            task_id="normal-task",
            run_id="run-safety",
            state_id=element.state_id,
            element_id=element.element_id,
            action=ExplorationAction.TAP,
            sequence=1,
        )
        dangerous_task = ExplorationTask(
            task_id="dangerous-task",
            run_id="run-safety",
            state_id=dangerous.state_id,
            element_id=dangerous.element_id,
            action=ExplorationAction.TAP,
            sequence=1,
        )

        normal_risk, blocked, _ = classifier.classify(normal_task, element)
        dangerous_risk, dangerous_status, _ = classifier.classify(
            dangerous_task,
            dangerous,
        )

        assert normal_risk is RiskLevel.LOW
        assert blocked is None
        assert dangerous_risk is RiskLevel.HIGH
        assert dangerous_status is ExplorationTaskStatus.BLOCKED


def test_engine_rejects_starting_outside_target_app(tmp_path: Any) -> None:
    workspace = ProjectWorkspace(tmp_path)
    map_id = MapId(APP_ID, Platform.ANDROID, "9.0.0", "6", "zh-CN")
    driver = FakeDriver(initial_hierarchy=EXTERNAL_SCREEN)

    with MapStore.create(workspace, map_id) as store:
        with pytest.raises(ValueError, match="outside the target app"):
            ExplorationEngine(
                store=store,
                driver=driver,
                run_id="run-invalid-start",
                target_app_id=APP_ID,
            )
