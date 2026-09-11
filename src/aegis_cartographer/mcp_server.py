"""Modern MCP facade for project-local Aegis maps and workers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from aegis_cartographer.core.models import Platform
from aegis_cartographer.core.query import (
    ElementQueryResult,
    MapQueryService,
    PathPlan,
    ScreenQueryResult,
)
from aegis_cartographer.core.storage import (
    MapId,
    MapStore,
    ProjectWorkspace,
)
from aegis_cartographer.worker.artifacts import export_map_archive
from aegis_cartographer.worker.config import load_project_config
from aegis_cartographer.worker.coverage import task_report
from aegis_cartographer.worker.current import identify_current_page
from aegis_cartographer.worker.selector import MapSelection, resolve_map_id


class MCPValidationError(ValueError):
    """Raised when a tool request is structurally invalid."""


class MCPNotFoundError(LookupError):
    """Raised when a requested project resource does not exist."""


class MCPCliError(RuntimeError):
    """Raised when the project CLI subprocess fails."""


_ROOT_DESCRIPTION = "业务项目根目录的绝对路径"
_MAP_SELECTOR_DESCRIPTION = (
    "地图选择器，格式：app@platform:version+build/locale，"
    "例如 com.example@android:1.2.0+12000/zh-CN。省略时使用项目默认地图。"
)


def _json_output(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _absolute_project_root(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MCPValidationError("project_root must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise MCPValidationError("project_root must be an absolute path")
    if not path.exists() or not path.is_dir():
        raise MCPNotFoundError(f"Project root does not exist or is not a directory: {path}")
    return path


def _workspace(value: Any) -> ProjectWorkspace:
    return ProjectWorkspace(_absolute_project_root(value))


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MCPValidationError(f"{field} must be a non-empty string when provided")
    return value


def _required_string(value: Any, field: str) -> str:
    result = _optional_string(value, field)
    if result is None:
        raise MCPValidationError(f"{field} is required")
    return result


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MCPValidationError(f"{field} must be an integer")
    if value < 1 or value > maximum:
        raise MCPValidationError(f"{field} must be between 1 and {maximum}")
    return value


def _reject_unknown_fields(
    name: str,
    arguments: Mapping[str, Any],
    allowed: set[str],
) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise MCPValidationError(f"Unknown fields for {name}: {', '.join(unknown)}")


def _map_selection(arguments: Mapping[str, Any]) -> MapSelection:
    platform_raw = arguments.get("platform")
    if platform_raw is not None and not isinstance(platform_raw, str):
        raise MCPValidationError("platform must be a string when provided")
    if platform_raw is not None:
        try:
            platform = Platform(platform_raw.lower())
        except ValueError as error:
            raise MCPValidationError(f"Unsupported platform: {platform_raw}") from error
    else:
        platform = None
    return MapSelection(
        selector=_optional_string(arguments.get("map"), "map"),
        app_id=_optional_string(arguments.get("app_id"), "app_id"),
        platform=platform,
        app_version=_optional_string(arguments.get("app_version"), "app_version"),
        build_number=_optional_string(arguments.get("build_number"), "build_number"),
        locale=_optional_string(arguments.get("locale"), "locale"),
    )


def _resolve_map(workspace: ProjectWorkspace, arguments: Mapping[str, Any]) -> MapId:
    try:
        return resolve_map_id(load_project_config(workspace), _map_selection(arguments))
    except ValueError as error:
        raise MCPValidationError(str(error)) from error


def _open_readonly_map(workspace: ProjectWorkspace, map_id: MapId) -> MapStore:
    try:
        return MapStore.open(workspace, map_id, readonly=True)
    except FileNotFoundError as error:
        raise MCPNotFoundError(str(error)) from error
    except ValueError as error:
        raise MCPValidationError(str(error)) from error


def _serialize_locator(locator: Any) -> dict[str, Any]:
    return {
        "locator_id": locator.locator_id,
        "platform": locator.platform.value,
        "strategy": locator.strategy,
        "value": locator.value,
        "priority": locator.priority,
        "scope": locator.scope,
        "success_count": locator.success_count,
        "attempt_count": locator.attempt_count,
        "last_error": locator.last_error,
        "last_verified_at": locator.last_verified_at,
    }


def _serialize_element(element: Any) -> dict[str, Any]:
    return {
        "element_id": element.element_id,
        "element_key": element.element_key,
        "screen_id": element.screen_id,
        "state_id": element.state_id,
        "canonical_element_id": element.canonical_element_id,
        "semantic_name": element.semantic_name,
        "aliases": list(element.aliases),
        "role": element.role,
        "is_actionable": element.is_actionable,
        "is_dynamic": element.is_dynamic,
        "risk_level": element.risk_level,
        "actions": [action.value for action in element.actions],
        "preconditions": list(element.preconditions),
        "text": element.text,
        "resource_id": element.resource_id,
        "accessibility_id": element.accessibility_id,
        "content_desc": element.content_desc,
        "class_name": element.class_name,
        "bounds": list(element.bounds),
        "occurrence_index": element.occurrence_index,
        "selector": element.selector,
        "confidence": element.confidence,
        "first_seen_at": element.first_seen_at,
        "last_verified_at": element.last_verified_at,
        "locators": [_serialize_locator(locator) for locator in element.locators],
    }


def _serialize_path(path: PathPlan | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {
        "start_state_id": path.start_state_id,
        "target_state_id": path.target_state_id,
        "target_type": path.target_type,
        "target_id": path.target_id,
        "step_count": len(path.steps),
        "total_cost": path.total_cost,
        "reliability": path.reliability,
        "steps": [
            {
                "transition_id": step.transition.transition_id,
                "from_state_id": step.from_state_id,
                "to_state_id": step.to_state_id,
                "element_id": step.element_id,
                "action": step.action,
                "result_type": step.result_type,
                "maestro_commands": list(step.maestro_commands),
                "restore_strategy": list(step.restore_strategy),
                "reliability": step.reliability,
            }
            for step in path.steps
        ],
    }


def _serialize_element_result(value: ElementQueryResult) -> dict[str, Any]:
    screen = None
    if value.screen is not None:
        screen = {
            "screen_id": value.screen.screen_id,
            "semantic_name": value.screen.semantic_name,
            "screen_type": value.screen.screen_type,
            "business_domain": value.screen.business_domain,
            "description": value.screen.description,
            "coverage_status": value.screen.coverage_status,
            "aliases": list(value.screen.aliases),
            "confidence": value.screen.confidence,
        }
    return {
        "query": value.query,
        "score": value.score,
        "matched_source": value.matched_source,
        "element": _serialize_element(value.element),
        "screen": screen,
        "path": _serialize_path(value.path),
    }


def _serialize_screen_result(value: ScreenQueryResult) -> dict[str, Any]:
    return {
        "query": value.query,
        "score": value.score,
        "matched_source": value.matched_source,
        "screen": {
            "screen_id": value.screen.screen_id,
            "semantic_name": value.screen.semantic_name,
            "screen_type": value.screen.screen_type,
            "business_domain": value.screen.business_domain,
            "description": value.screen.description,
            "coverage_status": value.screen.coverage_status,
            "aliases": list(value.screen.aliases),
            "confidence": value.screen.confidence,
            "state_ids": list(value.state_ids),
        },
        "path": _serialize_path(value.path),
    }


def _run_cli(arguments: Sequence[str]) -> dict[str, Any]:
    command = [sys.executable, "-m", "aegis_cartographer.aegis_cli", *arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise MCPCliError("Project CLI timed out") from error
    except OSError as error:
        raise MCPCliError(f"Unable to start project CLI: {error}") from error

    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise MCPCliError(
            "Project CLI returned invalid JSON",
        ) from error
    if not isinstance(output, dict):
        raise MCPCliError("Project CLI output must be a JSON object")
    output.setdefault("success", completed.returncode == 0)
    output["exit_code"] = completed.returncode
    output["stderr"] = completed.stderr
    if completed.returncode != 0 and output.get("success") is not False:
        output["success"] = False
    return output


def _map_cli_arguments(arguments: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    selector = _optional_string(arguments.get("map"), "map")
    if selector is not None:
        result.extend(["--map", selector])
    for source, target in (
        ("app_id", "app-id"),
        ("platform", "platform"),
        ("app_version", "app-version"),
        ("build_number", "build-number"),
        ("locale", "locale"),
    ):
        value = _optional_string(arguments.get(source), source)
        if value is not None:
            result.extend([f"--{target}", value])
    return result


def _safe_run_id(value: Any) -> str:
    run_id = _required_string(value, "run_id")
    if Path(run_id).name != run_id or run_id in {".", ".."}:
        raise MCPValidationError("run_id must be a safe single path component")
    return run_id


class ModernMCPServerLogic:
    """Transport-independent tool logic for the modern Aegis MCP surface."""

    def execute_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one MCP tool and return a structured JSON-serializable result."""

        if not isinstance(arguments, Mapping):
            raise MCPValidationError("Tool arguments must be an object")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise MCPValidationError(f"Unknown tool: {name}")
        return handler(arguments)

    def _tool_list_project_maps(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            "list_project_maps",
            arguments,
            {"project_root"},
        )
        workspace = _workspace(arguments.get("project_root"))
        maps = MapStore.list_maps(workspace)
        return {
            "success": True,
            "project_root": str(workspace.root),
            "count": len(maps),
            "maps": [
                {
                    "map_id": map_id.cache_key,
                    "directory": map_id.relative_directory.as_posix(),
                    "app_id": map_id.app_id,
                    "platform": map_id.platform.value,
                    "app_version": map_id.app_version,
                    "build_number": map_id.build_number,
                    "locale": map_id.locale,
                }
                for map_id in maps
            ],
        }

    def _tool_get_map_metadata(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "map", "app_id", "platform",
            "app_version", "build_number", "locale",
        }
        _reject_unknown_fields("get_map_metadata", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        store = _open_readonly_map(workspace, map_id)
        try:
            entry = store.get_entry_state()
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "manifest": store.manifest.to_dict(),
                "statistics": store.statistics(),
                "entry_state_id": None if entry is None else entry.state_id,
                "map_directory": map_id.relative_directory.as_posix(),
            }
        finally:
            store.close()

    def _tool_list_pages(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "map", "app_id", "platform",
            "app_version", "build_number", "locale",
        }
        _reject_unknown_fields("list_pages", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        store = _open_readonly_map(workspace, map_id)
        try:
            pages = [page.to_dict() for page in store.list_page_summaries()]
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "page_count": len(pages),
                "pages": pages,
            }
        finally:
            store.close()

    def _tool_list_pending_tasks(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "map", "app_id", "platform",
            "app_version", "build_number", "locale", "limit",
        }
        _reject_unknown_fields("list_pending_tasks", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        limit = arguments.get("limit", 20)
        if limit is None:
            limit = 20
        limit_value = _positive_int(limit, "limit", maximum=1000)
        store = _open_readonly_map(workspace, map_id)
        try:
            report = task_report(workspace, store)
        finally:
            store.close()
        for page in report["pages"]:
            tasks = page["tasks"]
            if len(tasks) > limit_value:
                page["tasks_truncated"] = len(tasks) - limit_value
                page["tasks"] = tasks[:limit_value]
            else:
                page["tasks_truncated"] = 0
        return {
            "success": True,
            "map_id": map_id.cache_key,
            **report,
        }

    @staticmethod
    def _resolve_page_id(store: MapStore, selector: str) -> str:
        exact = store.get_screen(selector)
        if exact is not None:
            return exact.screen_id
        matches = [
            page for page in store.list_page_summaries()
            if page.name == selector or selector in page.aliases
        ]
        if len(matches) != 1:
            raise MCPValidationError(f"Unknown or ambiguous page: {selector}")
        return matches[0].screen_id

    def _tool_get_page_route(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "map", "app_id", "platform", "app_version",
            "build_number", "locale", "from_page", "from_state_id", "to_page",
        }
        _reject_unknown_fields("get_page_route", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        from_page = _optional_string(arguments.get("from_page"), "from_page")
        from_state_id = _optional_string(
            arguments.get("from_state_id"),
            "from_state_id",
        )
        to_page = _required_string(arguments.get("to_page"), "to_page")
        store = _open_readonly_map(workspace, map_id)
        try:
            target_screen_id = self._resolve_page_id(store, to_page)
            if from_state_id is not None:
                start_state = store.get_screen_state(from_state_id)
                if start_state is None:
                    raise MCPValidationError(f"Unknown start state: {from_state_id}")
            elif from_page is not None:
                from_screen_id = self._resolve_page_id(store, from_page)
                states = store.list_screen_states(screen_id=from_screen_id)
                if not states:
                    raise MCPValidationError(f"Page has no states: {from_page}")
                start_state = states[0]
            else:
                start_state = store.get_entry_state()
                if start_state is None:
                    raise MCPValidationError("Map has no entry state")
            path = MapQueryService(store).find_screen_path(
                target_screen_id,
                start_state_id=start_state.state_id,
            )
            target_screen = store.get_screen(target_screen_id)
            from_screen = store.get_screen(start_state.screen_id)
            return {
                "success": path is not None,
                "map_id": map_id.cache_key,
                "from_page": None if from_screen is None else {
                    "screen_id": from_screen.screen_id,
                    "name": from_screen.semantic_name,
                },
                "to_page": None if target_screen is None else {
                    "screen_id": target_screen.screen_id,
                    "name": target_screen.semantic_name,
                },
                "reachable": path is not None,
                "path": _serialize_path(path),
                "maestro_flow": None if path is None else MapQueryService(
                    store
                ).generate_maestro_flow(path),
            }
        finally:
            store.close()

    def _tool_identify_current_page(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "map", "app_id", "platform",
            "app_version", "build_number", "locale",
        }
        _reject_unknown_fields("identify_current_page", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        config = load_project_config(workspace)
        if config is None:
            raise MCPValidationError("Project is not initialized")
        result = identify_current_page(
            workspace=workspace,
            map_id=map_id,
            config=config,
        )
        result.setdefault("success", False)
        return result

    def _query_store(
        self,
        arguments: Mapping[str, Any],
        allowed: set[str],
    ) -> tuple[ProjectWorkspace, MapId, MapStore, MapQueryService]:
        _reject_unknown_fields("query", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        store = _open_readonly_map(workspace, map_id)
        return workspace, map_id, store, MapQueryService(store)

    def _tool_query_elements(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "query", "limit", "map", "app_id",
            "platform", "app_version", "build_number", "locale",
        }
        query = _required_string(arguments.get("query"), "query")
        limit = arguments.get("limit", 10)
        if limit is None:
            limit = 10
        limit_value = _positive_int(limit, "limit", maximum=100)
        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            results = service.locate_elements(query, limit=limit_value)
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "query": query,
                "count": len(results),
                "results": [_serialize_element_result(value) for value in results],
            }
        finally:
            store.close()

    def _tool_query_screens(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "query", "limit", "map", "app_id",
            "platform", "app_version", "build_number", "locale",
        }
        query = _required_string(arguments.get("query"), "query")
        limit_value = _positive_int(arguments.get("limit", 10), "limit", maximum=100)
        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            results = service.locate_screens(query, limit=limit_value)
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "query": query,
                "count": len(results),
                "results": [_serialize_screen_result(value) for value in results],
            }
        finally:
            store.close()

    def _tool_locate_element(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "query", "limit", "map", "app_id",
            "platform", "app_version", "build_number", "locale",
        }
        query = _required_string(arguments.get("query"), "query")
        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            results = service.locate_elements(query, limit=1)
            if not results:
                raise MCPNotFoundError(f"No element matches query: {query}")
            result = results[0]
            flow = (
                None
                if result.path is None
                else service.generate_maestro_flow(result.path, include_target_tap=False)
            )
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "result": _serialize_element_result(result),
                "maestro_flow": flow,
            }
        finally:
            store.close()

    def _tool_get_element_locators(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "element_id", "map", "app_id",
            "platform", "app_version", "build_number", "locale",
        }
        element_id = _required_string(arguments.get("element_id"), "element_id")
        _workspace_obj, map_id, store, _service = self._query_store(arguments, allowed)
        try:
            element = store.get_element(element_id)
            if element is None:
                raise MCPNotFoundError(f"Unknown element: {element_id}")
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "element": _serialize_element(element),
            }
        finally:
            store.close()

    def _tool_get_path_to_element(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "element_id", "start_state_id", "map",
            "app_id", "platform", "app_version", "build_number", "locale",
        }
        element_id = _required_string(arguments.get("element_id"), "element_id")
        start_state_id = _optional_string(arguments.get("start_state_id"), "start_state_id")
        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            if store.get_element(element_id) is None:
                raise MCPNotFoundError(f"Unknown element: {element_id}")
            path = service.find_element_path(
                element_id,
                start_state_id=start_state_id,
            )
            if path is None:
                return {
                    "success": True,
                    "reachable": False,
                    "map_id": map_id.cache_key,
                    "path": None,
                }
            return {
                "success": True,
                "reachable": True,
                "map_id": map_id.cache_key,
                "path": _serialize_path(path),
                "maestro_flow": service.generate_maestro_flow(path, include_target_tap=False),
            }
        finally:
            store.close()

    def _tool_get_path_to_screen(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "screen_id", "start_state_id", "map",
            "app_id", "platform", "app_version", "build_number", "locale",
        }
        screen_id = _required_string(arguments.get("screen_id"), "screen_id")
        start_state_id = _optional_string(arguments.get("start_state_id"), "start_state_id")
        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            if not store.list_screen_states(screen_id=screen_id):
                raise MCPNotFoundError(f"Unknown screen: {screen_id}")
            path = service.find_screen_path(
                screen_id,
                start_state_id=start_state_id,
            )
            if path is None:
                return {
                    "success": True,
                    "reachable": False,
                    "map_id": map_id.cache_key,
                    "path": None,
                }
            return {
                "success": True,
                "reachable": True,
                "map_id": map_id.cache_key,
                "path": _serialize_path(path),
                "maestro_flow": service.generate_maestro_flow(path),
            }
        finally:
            store.close()

    def _tool_generate_maestro_flow(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "element_id", "screen_id", "include_target_tap",
            "map", "app_id", "platform", "app_version", "build_number", "locale",
        }
        element_id = _optional_string(arguments.get("element_id"), "element_id")
        screen_id = _optional_string(arguments.get("screen_id"), "screen_id")
        if (element_id is None) == (screen_id is None):
            raise MCPValidationError("Provide exactly one of element_id or screen_id")
        include_target_tap = arguments.get("include_target_tap", False)
        if not isinstance(include_target_tap, bool):
            raise MCPValidationError("include_target_tap must be boolean")
        if screen_id is not None and include_target_tap:
            raise MCPValidationError("include_target_tap requires element_id")

        _workspace_obj, map_id, store, service = self._query_store(arguments, allowed)
        try:
            if element_id is not None and store.get_element(element_id) is None:
                raise MCPNotFoundError(f"Unknown element: {element_id}")
            if screen_id is not None and not store.list_screen_states(screen_id=screen_id):
                raise MCPNotFoundError(f"Unknown screen: {screen_id}")
            path = (
                service.find_element_path(element_id)
                if element_id is not None
                else service.find_screen_path(screen_id or "")
            )
            if path is None:
                raise MCPNotFoundError("Target is not reachable from the map entry state")
            return {
                "success": True,
                "map_id": map_id.cache_key,
                "path": _serialize_path(path),
                "maestro_flow": service.generate_maestro_flow(
                    path,
                    include_target_tap=include_target_tap,
                ),
            }
        finally:
            store.close()

    def _tool_export_map(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "output", "map", "app_id", "platform",
            "app_version", "build_number", "locale",
        }
        _reject_unknown_fields("export_map", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        map_id = _resolve_map(workspace, arguments)
        output = _optional_string(arguments.get("output"), "output")
        if output is not None:
            output_path = Path(output).expanduser()
            if not output_path.is_absolute():
                output_path = workspace.root / output_path
            try:
                resolved_output = output_path.resolve()
                resolved_output.relative_to(workspace.root.resolve())
            except ValueError as error:
                raise MCPValidationError("export output must stay inside the project root") from error
            if output_path.is_symlink():
                raise MCPValidationError("export output must not be a symlink")
            output = str(resolved_output)
        try:
            archive = export_map_archive(workspace, map_id, output)
        except FileNotFoundError as error:
            raise MCPNotFoundError(str(error)) from error
        return {
            "success": True,
            "map_id": map_id.cache_key,
            "archive": str(archive),
        }

    def _tool_start_exploration(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "project_root", "run_id", "max_actions", "chunk_steps", "map",
            "app_id", "platform", "app_version", "build_number", "locale",
        }
        _reject_unknown_fields("start_exploration", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        _resolve_map(workspace, arguments)
        command = ["explore", "start", "--project", str(workspace.root)]
        command.extend(_map_cli_arguments(arguments))
        run_id = _optional_string(arguments.get("run_id"), "run_id")
        if run_id is not None:
            _safe_run_id(run_id)
            command.extend(["--run-id", run_id])
        max_actions = arguments.get("max_actions")
        if max_actions is not None:
            command.extend(["--max-actions", str(_positive_int(max_actions, "max_actions", 10_000_000))])
        chunk_steps = arguments.get("chunk_steps", 10)
        command.extend(["--chunk-steps", str(_positive_int(chunk_steps, "chunk_steps", 10_000))])
        result = _run_cli(command)
        result["async"] = True
        return result

    def _tool_get_exploration_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            "get_exploration_status",
            arguments,
            {"project_root", "run_id"},
        )
        workspace = _workspace(arguments.get("project_root"))
        run_id = _safe_run_id(arguments.get("run_id"))
        result = _run_cli(
            [
                "explore",
                "status",
                "--project",
                str(workspace.root),
                "--run-id",
                run_id,
            ]
        )
        result["success"] = result.get("success", True) and result.get("exit_code") == 0
        return result

    def _tool_resume_exploration(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"project_root", "run_id", "max_actions", "chunk_steps"}
        _reject_unknown_fields("resume_exploration", arguments, allowed)
        workspace = _workspace(arguments.get("project_root"))
        run_id = _safe_run_id(arguments.get("run_id"))
        command = [
            "explore",
            "resume",
            "--project",
            str(workspace.root),
            "--run-id",
            run_id,
        ]
        if arguments.get("max_actions") is not None:
            command.extend(
                [
                    "--max-actions",
                    str(_positive_int(arguments["max_actions"], "max_actions", 10_000_000)),
                ]
            )
        command.extend(
            ["--chunk-steps", str(_positive_int(arguments.get("chunk_steps", 10), "chunk_steps", 10_000))]
        )
        result = _run_cli(command)
        result["async"] = True
        return result

    def _signal_exploration(
        self,
        arguments: Mapping[str, Any],
        command_name: str,
    ) -> dict[str, Any]:
        _reject_unknown_fields(command_name, arguments, {"project_root", "run_id"})
        workspace = _workspace(arguments.get("project_root"))
        run_id = _safe_run_id(arguments.get("run_id"))
        return _run_cli(
            [
                "explore",
                command_name,
                "--project",
                str(workspace.root),
                "--run-id",
                run_id,
            ]
        )

    def _tool_pause_exploration(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._signal_exploration(arguments, "pause")

    def _tool_stop_exploration(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._signal_exploration(arguments, "stop")

    def _tool_get_coverage_report(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._tool_get_exploration_status(arguments)


def _schema(
    properties: Mapping[str, Any],
    required: Sequence[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _root_property() -> dict[str, Any]:
    return {"type": "string", "description": _ROOT_DESCRIPTION}


def _map_properties() -> dict[str, Any]:
    return {
        "map": {"type": "string", "description": _MAP_SELECTOR_DESCRIPTION},
        "app_id": {"type": "string", "description": "App ID / package / bundle ID"},
        "platform": {
            "type": "string",
            "enum": [item.value for item in Platform],
            "description": "目标平台",
        },
        "app_version": {"type": "string"},
        "build_number": {"type": "string"},
        "locale": {"type": "string"},
    }


def tool_definitions() -> list[types.Tool]:
    """Return the modern MCP tool surface."""

    root = _root_property()
    map_properties = _map_properties()
    return [
        types.Tool(
            name="list_project_maps",
            description="列出业务项目 .aegis/maps 中所有独立元素地图。",
            inputSchema=_schema({"project_root": root}, ["project_root"]),
        ),
        types.Tool(
            name="get_map_metadata",
            description="查看地图版本、统计信息和入口状态。",
            inputSchema=_schema(
                {"project_root": root, **map_properties},
                ["project_root"],
            ),
        ),
        types.Tool(
            name="list_pages",
            description="列出地图中的中文页面卡片、页面状态和元素统计。",
            inputSchema=_schema(
                {"project_root": root, **map_properties},
                ["project_root"],
            ),
        ),
        types.Tool(
            name="list_pending_tasks",
            description="按中文页面汇总历史 run 中未完成的遍历任务。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 20,
                    },
                },
                ["project_root"],
            ),
        ),
        types.Tool(
            name="get_page_route",
            description="规划从当前/指定页面到目标页面的可执行路径。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "from_page": {"type": "string", "description": "起始页面名、别名或 screen_id"},
                    "from_state_id": {"type": "string"},
                    "to_page": {"type": "string", "description": "目标页面名、别名或 screen_id"},
                },
                ["project_root", "to_page"],
            ),
        ),
        types.Tool(
            name="identify_current_page",
            description="读取当前手机页面并匹配地图中的页面；只观察，不点击。",
            inputSchema=_schema(
                {"project_root": root, **map_properties},
                ["project_root"],
            ),
        ),
        types.Tool(
            name="query_elements",
            description="按语义、文案、ID 查询元素，并返回页面、locator 和到达路径。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "query": {"type": "string", "description": "自然语言 / 文案 / ID 查询"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
                ["project_root", "query"],
            ),
        ),
        types.Tool(
            name="query_screens",
            description="按页面语义查询页面及其状态。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
                ["project_root", "query"],
            ),
        ),
        types.Tool(
            name="locate_element",
            description="返回单个最佳元素匹配、locator、页面、路径和 Maestro flow。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "query": {"type": "string"},
                },
                ["project_root", "query"],
            ),
        ),
        types.Tool(
            name="get_element_locators",
            description="按 element_id 获取元素全部 locator 和验证统计。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "element_id": {"type": "string"},
                },
                ["project_root", "element_id"],
            ),
        ),
        types.Tool(
            name="get_path_to_element",
            description="规划从地图入口到指定元素的路径。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "element_id": {"type": "string"},
                    "start_state_id": {"type": "string"},
                },
                ["project_root", "element_id"],
            ),
        ),
        types.Tool(
            name="get_path_to_screen",
            description="规划从地图入口到指定页面的路径。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "screen_id": {"type": "string"},
                    "start_state_id": {"type": "string"},
                },
                ["project_root", "screen_id"],
            ),
        ),
        types.Tool(
            name="generate_maestro_flow",
            description="根据地图路径生成安全的 Maestro flow。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "element_id": {"type": "string"},
                    "screen_id": {"type": "string"},
                    "include_target_tap": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否在元素路径末尾追加目标点击",
                    },
                },
                ["project_root"],
            ),
        ),
        types.Tool(
            name="export_map",
            description="把项目本地地图导出为自包含 .aegis-map.zip。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "output": {"type": "string", "description": "输出 zip 路径；省略使用项目默认位置"},
                },
                ["project_root"],
            ),
        ),
        types.Tool(
            name="start_exploration",
            description="启动后台 Maestro DFS 遍历 Worker，立即返回 run_id，不等待遍历完成。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    **map_properties,
                    "run_id": {"type": "string"},
                    "max_actions": {"type": "integer", "minimum": 1},
                    "chunk_steps": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 10},
                },
                ["project_root"],
            ),
        ),
        types.Tool(
            name="get_exploration_status",
            description="获取遍历 run 的 checkpoint、任务状态、地图统计和进程状态。",
            inputSchema=_schema(
                {"project_root": root, "run_id": {"type": "string"}},
                ["project_root", "run_id"],
            ),
        ),
        types.Tool(
            name="resume_exploration",
            description="恢复一个暂停或中断的遍历 run。",
            inputSchema=_schema(
                {
                    "project_root": root,
                    "run_id": {"type": "string"},
                    "max_actions": {"type": "integer", "minimum": 1},
                    "chunk_steps": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
                ["project_root", "run_id"],
            ),
        ),
        types.Tool(
            name="pause_exploration",
            description="请求后台遍历 Worker 在当前步骤后安全暂停。",
            inputSchema=_schema(
                {"project_root": root, "run_id": {"type": "string"}},
                ["project_root", "run_id"],
            ),
        ),
        types.Tool(
            name="stop_exploration",
            description="请求后台遍历 Worker 在当前步骤后安全停止。",
            inputSchema=_schema(
                {"project_root": root, "run_id": {"type": "string"}},
                ["project_root", "run_id"],
            ),
        ),
        types.Tool(
            name="get_coverage_report",
            description="获取遍历 run 的覆盖率报告，等价于状态报告。",
            inputSchema=_schema(
                {"project_root": root, "run_id": {"type": "string"}},
                ["project_root", "run_id"],
            ),
        ),
    ]


async def serve() -> None:
    """Run the modern Aegis MCP server over stdio."""

    logic = ModernMCPServerLogic()

    async def handle_call_tool(
        _context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        try:
            result = logic.execute_tool(params.name, params.arguments or {})
        except MCPValidationError as error:
            result = {
                "success": False,
                "error": {"code": "VALIDATION_ERROR", "message": str(error)},
            }
        except MCPNotFoundError as error:
            result = {
                "success": False,
                "error": {"code": "NOT_FOUND", "message": str(error)},
            }
        except MCPCliError as error:
            result = {
                "success": False,
                "error": {"code": "CLI_ERROR", "message": str(error)},
            }
        except Exception as error:
            result = {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_json_output(result))],
            structuredContent=result,
            isError=not result.get("success", False),
        )

    async def handle_list_tools(
        _context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_definitions())

    server = Server(
        "aegis-cartographer",
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


__all__ = [
    "MCPNotFoundError",
    "MCPCliError",
    "MCPValidationError",
    "ModernMCPServerLogic",
    "serve",
    "tool_definitions",
]
