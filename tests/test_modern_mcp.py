from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aegis_cartographer.core.storage import MapStore, ProjectWorkspace
from aegis_cartographer.mcp_server import (
    MCPNotFoundError,
    MCPValidationError,
    ModernMCPServerLogic,
    tool_definitions,
)
from tests.test_worker_cli import MAP_ID, create_explored_workspace


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    workspace = create_explored_workspace(tmp_path)
    return workspace.root


@pytest.fixture()
def logic() -> ModernMCPServerLogic:
    return ModernMCPServerLogic()


def test_lists_one_isolated_project_map(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    result = logic.execute_tool(
        "list_project_maps",
        {"project_root": str(project)},
    )

    assert result["success"] is True
    assert result["count"] == 1
    assert result["maps"][0]["map_id"] == MAP_ID.cache_key


def test_map_metadata_contains_manifest_statistics_and_entry(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    result = logic.execute_tool(
        "get_map_metadata",
        {"project_root": str(project), "map": MAP_ID.cache_key},
    )

    assert result["success"] is True
    assert result["manifest"]["map_id"] == MAP_ID.to_dict()
    assert result["statistics"]["screen_states"] == 2
    assert result["entry_state_id"]


def test_query_and_locate_element_return_locator_path_and_flow(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    query = {
        "project_root": str(project),
        "map": MAP_ID.cache_key,
        "query": "详情按钮",
    }

    queried = logic.execute_tool("query_elements", query)
    located = logic.execute_tool("locate_element", query)

    assert queried["count"] == 1
    assert queried["results"][0]["path"]["step_count"] == 1
    assert located["result"]["element"]["resource_id"] == "com.example:id/detail_noop"
    assert "tapOn" in located["maestro_flow"]


def test_query_screens_returns_states(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    result = logic.execute_tool(
        "query_screens",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "query": "详情页",
        },
    )

    assert result["success"] is True
    assert result["results"]
    assert result["results"][0]["screen"]["state_ids"]


def test_list_pages_returns_human_readable_cards(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    result = logic.execute_tool(
        "list_pages",
        {"project_root": str(project), "map": MAP_ID.cache_key},
    )

    assert result["success"] is True
    assert result["page_count"] >= 1
    assert all(page["name"] for page in result["pages"])
    assert all("description" in page for page in result["pages"])


def test_pending_tasks_and_page_route_tools(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    pending = logic.execute_tool(
        "list_pending_tasks",
        {"project_root": str(project), "map": MAP_ID.cache_key},
    )
    route = logic.execute_tool(
        "get_page_route",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "from_page": "首页",
            "to_page": "详情页",
        },
    )

    assert pending["success"] is True
    assert pending["unfinished_tasks"] == 0
    assert route["success"] is True
    assert route["reachable"] is True
    assert route["path"]["step_count"] == 1


def test_element_locators_and_path_tools(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    with MapStore.open(
        ProjectWorkspace(project),
        MAP_ID,
        readonly=True,
    ) as store:
        element = next(
            item
            for item in store.list_elements()
            if item.resource_id == "com.example:id/detail_noop"
        )
        screen_id = element.screen_id

    element_result = logic.execute_tool(
        "get_element_locators",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "element_id": element.element_id,
        },
    )
    element_path = logic.execute_tool(
        "get_path_to_element",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "element_id": element.element_id,
        },
    )
    screen_path = logic.execute_tool(
        "get_path_to_screen",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "screen_id": screen_id,
        },
    )

    assert element_result["element"]["locators"]
    assert element_path["reachable"] is True
    assert element_path["path"]["step_count"] == 1
    assert screen_path["reachable"] is True


def test_generate_flow_rejects_ambiguous_target(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    with pytest.raises(MCPValidationError, match="exactly one"):
        logic.execute_tool(
            "generate_maestro_flow",
            {
                "project_root": str(project),
                "map": MAP_ID.cache_key,
                "element_id": "element",
                "screen_id": "screen",
            },
        )


def test_export_map_stays_inside_project(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    result = logic.execute_tool(
        "export_map",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "output": "exports/mcp-map.zip",
        },
    )
    assert result["success"] is True
    assert result["archive"].startswith(str(project))

    with pytest.raises(MCPValidationError, match="inside the project root"):
        logic.execute_tool(
            "export_map",
            {
                "project_root": str(project),
                "map": MAP_ID.cache_key,
                "output": "../outside.zip",
            },
        )


def test_exploration_control_uses_async_cli(
    logic: ModernMCPServerLogic,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    def fake_run_cli(arguments: list[str]) -> dict[str, Any]:
        captured.append(arguments)
        return {"success": True, "exit_code": 0, "run_id": "run-mcp"}

    monkeypatch.setattr("aegis_cartographer.mcp_server._run_cli", fake_run_cli)

    started = logic.execute_tool(
        "start_exploration",
        {
            "project_root": str(project),
            "map": MAP_ID.cache_key,
            "run_id": "run-mcp",
            "max_actions": 20,
            "chunk_steps": 2,
        },
    )
    status = logic.execute_tool(
        "get_exploration_status",
        {"project_root": str(project), "run_id": "run-mcp"},
    )
    resumed = logic.execute_tool(
        "resume_exploration",
        {"project_root": str(project), "run_id": "run-mcp"},
    )
    paused = logic.execute_tool(
        "pause_exploration",
        {"project_root": str(project), "run_id": "run-mcp"},
    )

    assert started["async"] is True
    assert status["success"] is True
    assert resumed["async"] is True
    assert paused["success"] is True
    assert captured[0][0:2] == ["explore", "start"]
    assert "--map" in captured[0]
    assert captured[1][0:2] == ["explore", "status"]
    assert captured[2][0:2] == ["explore", "resume"]
    assert captured[3][0:2] == ["explore", "pause"]


def test_validation_errors_are_structured(
    logic: ModernMCPServerLogic,
    project: Path,
) -> None:
    with pytest.raises(MCPValidationError, match="Unknown fields"):
        logic.execute_tool(
            "list_project_maps",
            {"project_root": str(project), "unexpected": True},
        )
    with pytest.raises(MCPValidationError, match="absolute path"):
        logic.execute_tool("list_project_maps", {"project_root": "relative/path"})
    with pytest.raises(MCPValidationError, match="Unknown tool"):
        logic.execute_tool("does_not_exist", {})
    with pytest.raises(MCPNotFoundError, match="No element matches"):
        logic.execute_tool(
            "locate_element",
            {
                "project_root": str(project),
                "map": MAP_ID.cache_key,
                "query": "绝对不存在的元素",
            },
        )


def test_modern_tool_schemas_are_strict() -> None:
    tools = tool_definitions()
    names = [tool.name for tool in tools]

    assert len(names) == len(set(names))
    assert "locate_element" in names
    assert "list_pages" in names
    assert "list_pending_tasks" in names
    assert "get_page_route" in names
    assert "identify_current_page" in names
    assert "start_exploration" in names
    assert all(tool.inputSchema["type"] == "object" for tool in tools)
    assert all(tool.inputSchema["additionalProperties"] is False for tool in tools)
