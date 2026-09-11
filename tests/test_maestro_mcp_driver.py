from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from aegis_cartographer.device.maestro_mcp_driver import MaestroMcpDriver


class FakeMcpSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        read_timeout_seconds: float | None = None,
        timeout: float | None = None,
    ) -> Any:
        self.calls.append((name, arguments, timeout or read_timeout_seconds))
        if name == "inspect_screen":
            payload = {
                "ui_schema": {
                    "platform": "android",
                    "abbreviations": {},
                    "defaults": {},
                },
                "elements": [
                    {
                        "cls": "android.widget.FrameLayout",
                        "c": [
                            {
                                "rid": "com.example:id/button",
                                "txt": "下一步",
                                "clk": True,
                                "b": "[0,0][100,50]",
                            }
                        ],
                    }
                ],
            }
            content = [SimpleNamespace(type="text", text=json.dumps(payload))]
        elif name == "run":
            content = [SimpleNamespace(type="text", text="flow passed")]
        else:
            raise AssertionError(f"unexpected tool: {name}")
        return SimpleNamespace(
            content=content,
            structuredContent=None,
            isError=False,
        )


def test_mcp_driver_reads_hierarchy_without_spawning_cli() -> None:
    driver = MaestroMcpDriver(
        maestro_path="/bin/echo",
        device_id="device-1",
        command_timeout=1.0,
        action_timeout=1.0,
        hierarchy_timeout=1.0,
        stability_timeout=1.0,
        back_timeout=1.0,
        launch_timeout=1.0,
    )
    session = FakeMcpSession()
    driver._session = session

    result = driver.get_hierarchy_result()

    assert result.success is True
    assert result.attempts == 1
    assert result.hierarchy["elements"]
    assert session.calls[0][0] == "inspect_screen"
    assert session.calls[0][1] == {"device_id": "device-1"}


def test_mcp_driver_executes_rendered_inline_flow() -> None:
    driver = MaestroMcpDriver(
        maestro_path="/bin/echo",
        device_id="device-1",
        command_timeout=1.0,
        action_timeout=1.0,
        hierarchy_timeout=1.0,
        stability_timeout=1.0,
        back_timeout=1.0,
        launch_timeout=1.0,
    )
    session = FakeMcpSession()
    driver._session = session

    result = driver.execute_flow(
        [
            {"tapOn": {"id": "com.example:id/button"}},
            {"waitForAnimationToEnd": {"timeout": 100}},
        ],
        timeout=1.0,
    )

    assert result["success"] is True
    assert session.calls[0][0] == "run"
    assert session.calls[0][1]["device_id"] == "device-1"
    assert '"tapOn":{"id":"com.example:id/button"}' in result["flow_yaml"]
    assert '"waitForAnimationToEnd":{"timeout":100}' in result["flow_yaml"]


def test_config_defaults_to_persistent_mcp_driver() -> None:
    from aegis_cartographer.worker.config import ProjectConfig

    config = ProjectConfig()
    assert config.maestro.driver_type == "maestro_mcp"
    assert config.maestro.mcp_no_viewer is True
