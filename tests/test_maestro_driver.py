from __future__ import annotations

import json
import os
from typing import Any, Sequence

import pytest

from aegis_cartographer.device.maestro_driver import MaestroDriver


class RecordingDriver(MaestroDriver):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cli_calls: list[tuple[Sequence[str], float | None]] = []

    def _run_cli(
        self,
        args: Sequence[str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.cli_calls.append((tuple(args), timeout))
        return {
            "success": True,
            "command": [self.maestro_bin, *args],
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "error": None,
            "duration_ms": 1,
        }


class FailingDriver(MaestroDriver):
    def _run_cli(
        self,
        args: Sequence[str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "command": [self.maestro_bin, *args],
            "stdout": "",
            "stderr": "element not found",
            "exit_code": 1,
            "error": "maestro_exit_error",
            "duration_ms": 2,
        }


class FlakyHierarchyDriver(MaestroDriver):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0

    def _run_cli(
        self,
        args: Sequence[str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "success": False,
                "command": [self.maestro_bin, *args],
                "stdout": "",
                "stderr": "temporary hierarchy failure",
                "exit_code": 1,
                "error": "maestro_exit_error",
                "duration_ms": 1,
            }
        return {
            "success": True,
            "command": [self.maestro_bin, *args],
            "stdout": json.dumps({"tree": {"class": "Window"}}),
            "stderr": "",
            "exit_code": 0,
            "error": None,
            "duration_ms": 1,
        }


def test_flow_is_safely_serialized_for_hostile_text(tmp_path: Any) -> None:
    malicious_text = '删除": true\n- injectedCommand: bad'
    malicious_app_id = 'com.example": true\n---\n- evil'
    driver = MaestroDriver(
        maestro_path="/bin/echo",
        app_id=malicious_app_id,
        flow_dir=str(tmp_path),
    )

    flow = driver._render_flow(
        [
            {"tapOn": {"text": malicious_text, "index": 1}},
            "waitForAnimationToEnd",
        ]
    )

    assert flow.startswith(
        json.dumps(
            {"appId": malicious_app_id},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert "\n- injectedCommand" not in flow
    assert malicious_text not in flow
    assert "- {\"tapOn\":{\"text\":" in flow
    assert flow.endswith("- waitForAnimationToEnd\n")


def test_successful_flows_use_unique_private_temp_files_and_are_removed(tmp_path: Any) -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    first = driver.tap_on(text="确认", occurrence_index=2)
    second = driver.tap_on(text="取消")

    assert first["success"] is True
    assert second["success"] is True
    assert first["flow_path"] != second["flow_path"]
    assert not os.path.exists(first["flow_path"])
    assert not os.path.exists(second["flow_path"])
    assert first["flow_retained"] is False
    assert first["command"] == ["/bin/echo", "test", first["flow_path"]]
    assert [call[1] for call in driver.cli_calls] == [40.0, 40.0]


def test_hierarchy_failure_is_retried_with_backoff() -> None:
    driver = FlakyHierarchyDriver(
        maestro_path="/bin/echo",
        hierarchy_retries=3,
        hierarchy_retry_backoff_ms=0,
    )

    result = driver.get_hierarchy_result()

    assert result.success is True
    assert result.attempts == 2
    assert result.error is None
    assert result.hierarchy == {"tree": {"class": "Window"}}


def test_hierarchy_retries_are_bounded_and_classified() -> None:
    class InvalidHierarchyDriver(FlakyHierarchyDriver):
        def _run_cli(
            self,
            args: Sequence[str],
            timeout: float | None = None,
        ) -> dict[str, Any]:
            result = super()._run_cli(args, timeout=timeout)
            result["success"] = True
            result["stdout"] = "not-json"
            return result

    driver = InvalidHierarchyDriver(
        maestro_path="/bin/echo",
        hierarchy_retries=2,
        hierarchy_retry_backoff_ms=0,
    )

    result = driver.get_hierarchy_result()

    assert result.success is False
    assert result.attempts == 2
    assert result.error == "hierarchy_invalid_json"


def test_failed_flow_is_retained_for_triage(tmp_path: Any) -> None:
    driver = FailingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    result = driver.tap_on(element_id="com.example:id/missing")

    assert result["success"] is False
    assert result["flow_retained"] is True
    assert os.path.exists(result["flow_path"])
    assert result["error"] == "maestro_exit_error"


def test_bounds_are_converted_to_center_point(tmp_path: Any) -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    result = driver.tap_on(bounds="[10,20][110,80]", occurrence_index=1)

    assert result["success"] is True
    assert '{"tapOn":{"point":"60,50","index":0}}' in result["flow_yaml"]


def test_long_press_scroll_back_and_keyboard_actions(tmp_path: Any) -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    long_press = driver.long_press_on(text="更多")
    swipe = driver.swipe("down")
    upward_scroll = driver.scroll("UP")
    back = driver.back()
    hide_keyboard = driver.hide_keyboard()

    results = [long_press, swipe, upward_scroll, back, hide_keyboard]
    assert all(result["success"] is True for result in results)
    assert '{"swipe":"UP"}' in upward_scroll["flow_yaml"]
    assert all(
        '{"waitForAnimationToEnd":{"timeout":3000}}' in result["flow_yaml"]
        for result in results
    )

    flows = [call[0][1] for call in driver.cli_calls]
    assert len(flows) == 5
    assert all(os.path.isabs(path) for path in flows)


def test_invalid_direction_and_selector_fail_without_cli(tmp_path: Any) -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    assert driver.swipe("diagonal")["error"] == "direction must be one of ['DOWN', 'LEFT', 'RIGHT', 'UP']"
    assert driver.tap_on()["error"] == (
        "A valid element_id, text, label, point, or bounds is required"
    )
    assert driver.tap_on(bounds="[10,20][5,80]")["error"] == (
        "A valid element_id, text, label, point, or bounds is required"
    )
    assert driver.cli_calls == []


def test_device_option_is_only_added_to_test_commands() -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        device_id="emulator-5554",
        app_id="com.example",
    )

    hierarchy_command = driver._build_cli_command(["hierarchy"])
    test_command = driver._build_cli_command(["test", "/tmp/flow.yaml"])

    assert hierarchy_command == ["/bin/echo", "hierarchy"]
    assert test_command == [
        "/bin/echo",
        "test",
        "/tmp/flow.yaml",
        "--device",
        "emulator-5554",
    ]


def test_launch_restart_and_input_commands(tmp_path: Any) -> None:
    driver = RecordingDriver(
        maestro_path="/bin/echo",
        app_id="com.example",
        flow_dir=str(tmp_path),
    )

    launch = driver.launch_app()
    restart = driver.restart_app()
    entered = driver.input_text(
        '用户"名\n- bad',
        element_id="com.example:id/username",
    )

    assert launch["success"] is True
    assert '{"launchApp":"com.example"}' in launch["flow_yaml"]
    assert '{"stopApp":"com.example"}' in restart["flow_yaml"]
    assert '"inputText":"用户\\"名\\n- bad"' in entered["flow_yaml"]
    assert '"id":"com.example:id/username"' in entered["flow_yaml"]


def test_missing_app_id_fails_cleanly(tmp_path: Any) -> None:
    driver = RecordingDriver(maestro_path="/bin/echo", flow_dir=str(tmp_path))

    assert driver.launch_app()["error"] == "app_id is not configured"
    assert driver.stop_app()["error"] == "app_id is not configured"
    assert driver.cli_calls == []


def test_wait_until_stable_ignores_dynamic_text() -> None:
    payloads = [
        {
            "tree": {
                "class": "Window",
                "children": [
                    {"class": "Button", "resource-id": "one", "text": "A", "clickable": True}
                ],
            }
        },
        {
            "tree": {
                "class": "Window",
                "children": [
                    {"class": "Button", "resource-id": "one", "text": "B", "clickable": True}
                ],
            }
        },
    ]

    class StableDriver(MaestroDriver):
        index = 0

        def get_hierarchy(self) -> dict[str, Any]:
            payload = payloads[min(self.index, len(payloads) - 1)]
            self.index += 1
            return payload

    driver = StableDriver(maestro_path="/bin/echo")
    result = driver.wait_until_stable(interval=0.001, stable_count=2)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["empty_observations"] == 0


def test_wait_until_stable_times_out_on_empty_hierarchy() -> None:
    class EmptyDriver(MaestroDriver):
        def get_hierarchy(self) -> dict[str, Any]:
            return {}

    driver = EmptyDriver(maestro_path="/bin/echo")
    result = driver.wait_until_stable(timeout=0.01, interval=0.001)

    assert result["success"] is False
    assert result["error"] == "timeout"


def test_wait_until_stable_reports_hierarchy_error_code() -> None:
    driver = FailingDriver(
        maestro_path="/bin/echo",
        hierarchy_retries=1,
        hierarchy_retry_backoff_ms=0,
    )

    result = driver.wait_until_stable(timeout=0.01, interval=0.001)

    assert result["success"] is False
    assert result["last_hierarchy_error"] == "hierarchy_maestro_exit_error"


def test_foreground_activity_is_parsed_from_adb_output() -> None:
    class ForegroundDriver(MaestroDriver):
        def _run_adb(self, args: Sequence[str], *, timeout: float = 5.0) -> dict[str, Any]:
            return {
                "success": True,
                "stdout": (
                    "mResumedActivity: ActivityRecord{939669 u0 "
                    "com.shark.jizhang/.module.main.MainActivity t208}"
                ),
                "stderr": "",
                "error": None,
                "duration_ms": 1,
            }

    driver = ForegroundDriver(
        maestro_path="/bin/echo",
        adb_path="/usr/bin/true",
        device_id="2NSDU20811003002",
    )

    result = driver.get_foreground_activity()

    assert result.success is True
    assert result.package_name == "com.shark.jizhang"
    assert result.activity_name == ".module.main.MainActivity"


def test_invalid_command_is_rejected_before_writing_file(tmp_path: Any) -> None:
    driver = MaestroDriver(
        maestro_path="/bin/echo",
        flow_dir=str(tmp_path),
    )

    with pytest.raises(ValueError, match="Unsafe or unsupported simple command"):
        driver._render_flow(["waitForAnimationToEnd: injected"])
