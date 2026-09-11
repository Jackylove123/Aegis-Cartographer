"""Command-line interface for Aegis Cartographer."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from aegis_cartographer.core.models import Platform
from aegis_cartographer.core.query import MapQueryService
from aegis_cartographer.core.storage import (
    MapId,
    MapStore,
    ProjectWorkspace,
)
from aegis_cartographer.worker.artifacts import export_map_archive, validate_map_archive
from aegis_cartographer.worker.config import (
    ExplorationConfig,
    MaestroConfig,
    ProjectConfig,
    SafetyConfig,
    load_project_config,
    save_project_config,
)
from aegis_cartographer.worker.coverage import task_report
from aegis_cartographer.worker.current import identify_current_page
from aegis_cartographer.worker.runner import run_exploration
from aegis_cartographer.worker.selector import MapSelection, resolve_map_id


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _add_map_arguments(parser: argparse.ArgumentParser, *, include_selector: bool = True) -> None:
    if include_selector:
        parser.add_argument(
            "--map",
            dest="map_selector",
            help="Map selector: app@platform:version+build/locale",
        )
    parser.add_argument("--project", default=".")
    parser.add_argument("--app-id")
    parser.add_argument(
        "--platform",
        choices=[item.value for item in Platform],
    )
    parser.add_argument("--app-version")
    parser.add_argument("--build-number")
    parser.add_argument("--locale")


def _selection(args: argparse.Namespace) -> MapSelection:
    return MapSelection(
        selector=getattr(args, "map_selector", None),
        app_id=args.app_id,
        platform=Platform(args.platform) if args.platform else None,
        app_version=args.app_version,
        build_number=args.build_number,
        locale=args.locale,
    )


def _map_id_from_args(args: argparse.Namespace) -> MapId:
    workspace = ProjectWorkspace(args.project)
    return resolve_map_id(load_project_config(workspace), _selection(args))


def _new_run_id() -> str:
    return (
        "run-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )


def _safe_run_id(run_id: str) -> str:
    if not run_id.strip() or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a safe single path component")
    return run_id


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _write_run_metadata(
    workspace: ProjectWorkspace,
    run_id: str,
    map_id: MapId,
    *,
    command: Sequence[str],
    foreground: bool,
) -> dict[str, Any]:
    run_directory = workspace.runs_dir / run_id
    run_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    value: dict[str, Any] = {
        "run_id": run_id,
        "map_id": map_id.to_dict(),
        "map_key": map_id.cache_key,
        "command": list(command),
        "foreground": foreground,
        "pid": None,
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "status": "REQUESTED",
    }
    workspace.write_json_atomic(run_directory / "run.json", value)
    return value


def _read_run_metadata(workspace: ProjectWorkspace, run_id: str) -> dict[str, Any]:
    path = workspace.runs_dir / run_id / "run.json"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown run: {run_id}")
    return _read_json(path)


def _update_run_metadata(
    workspace: ProjectWorkspace,
    run_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    value = _read_run_metadata(workspace, run_id)
    value.update(updates)
    workspace.write_json_atomic(workspace.runs_dir / run_id / "run.json", value)
    return value


def _process_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _checkpoint(workspace: ProjectWorkspace, run_id: str) -> dict[str, Any] | None:
    path = workspace.runs_dir / run_id / "checkpoint.json"
    return _read_json(path) if path.is_file() else None


def _task_statuses(workspace: ProjectWorkspace, run_id: str) -> dict[str, int]:
    database = workspace.runs_dir / run_id / "tasks.sqlite"
    if not database.is_file():
        return {}
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status ORDER BY status"
        ).fetchall()
    finally:
        connection.close()
    return {str(status): int(count) for status, count in rows}


def _map_statistics(workspace: ProjectWorkspace, map_id: MapId) -> dict[str, int] | None:
    manifest = workspace.map_directory(map_id) / "manifest.json"
    if not manifest.is_file():
        return None
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        return store.statistics()
    finally:
        store.close()


def _status_value(workspace: ProjectWorkspace, run_id: str) -> dict[str, Any]:
    metadata = _read_run_metadata(workspace, run_id)
    checkpoint = _checkpoint(workspace, run_id)
    map_id = MapId.from_dict(metadata["map_id"])
    pid = metadata.get("pid")
    return {
        "run_id": run_id,
        "map_id": metadata.get("map_key"),
        "process_alive": _process_alive(int(pid)) if pid is not None else False,
        "worker_status": metadata.get("status"),
        "pid": pid,
        "checkpoint": checkpoint,
        "task_statuses": _task_statuses(workspace, run_id),
        "map_statistics": _map_statistics(workspace, map_id),
    }


def _ensure_map(workspace: ProjectWorkspace, map_id: MapId) -> None:
    workspace.ensure_layout()
    manifest = workspace.map_directory(map_id) / "manifest.json"
    if manifest.is_file():
        store = MapStore.open(workspace, map_id, readonly=False)
    else:
        store = MapStore.create(workspace, map_id)
    store.close()


def _worker_command(
    args: argparse.Namespace,
    map_id: MapId,
    run_id: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "aegis_cartographer.worker",
        "--project",
        str(ProjectWorkspace(args.project).root),
        "--map",
        map_id.cache_key,
        "--run-id",
        run_id,
    ]
    if args.max_actions is not None:
        command.extend(["--max-actions", str(args.max_actions)])
    command.extend(["--chunk-steps", str(args.chunk_steps)])
    return command


def _spawn_worker(
    workspace: ProjectWorkspace,
    args: argparse.Namespace,
    map_id: MapId,
    run_id: str,
) -> dict[str, Any]:
    command = _worker_command(args, map_id, run_id)
    log_path = (
        workspace.logs_dir
        / "runs"
        / f"{run_id}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = _read_run_metadata(workspace, run_id)
    with log_path.open("a", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            cwd=str(workspace.root),
            start_new_session=True,
        )
    metadata = _update_run_metadata(
        workspace,
        run_id,
        {
            "pid": process.pid,
            "status": "RUNNING",
            "log_path": str(log_path.relative_to(workspace.root)),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        },
    )
    return {
        "run_id": run_id,
        "map_id": map_id.cache_key,
        "pid": process.pid,
        "status": "RUNNING",
        "log": str(log_path),
        "command": command,
        "metadata": metadata,
    }


def cmd_init(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    workspace.ensure_layout()
    existing = load_project_config(workspace)
    if existing is not None and not args.force:
        raise ValueError(f"Project config already exists: {workspace.config_path}")

    config = ProjectConfig(
        app_id=args.app_id,
        platform=Platform(args.platform),
        app_version=args.app_version,
        build_number=args.build_number,
        locale=args.locale,
        maestro=MaestroConfig(
            device_id=args.device_id,
            driver_type=args.driver_type,
            command_timeout=args.command_timeout,
            maestro_path=args.maestro_path,
            adb_path=args.adb_path,
            action_timeout=args.action_timeout,
            hierarchy_timeout=args.hierarchy_timeout,
            hierarchy_retries=args.hierarchy_retries,
            hierarchy_retry_backoff_ms=args.hierarchy_retry_backoff_ms,
            animation_wait_timeout_ms=args.animation_wait_timeout_ms,
            stability_timeout=args.stability_timeout,
            back_timeout=args.back_timeout,
            launch_timeout=args.launch_timeout,
        ),
        exploration=ExplorationConfig(
            max_actions=args.max_actions,
            max_depth=args.max_depth,
            max_states=args.max_states,
            max_errors=args.max_errors,
            max_retries=args.max_retries,
            max_restore_attempts=args.max_restore_attempts,
            max_consecutive_noops=args.max_consecutive_noops,
        ),
        safety=SafetyConfig(
            allow_input_text=args.allow_input_text,
            blocked_keywords=args.blocked_keyword,
            needs_human_keywords=args.needs_human_keyword,
        ),
    )
    path = save_project_config(workspace, config)
    _json_print(
        {
            "success": True,
            "config_path": str(path),
            "default_map": config.default_map_id().cache_key,
        }
    )
    return 0


def cmd_map_list(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    maps = MapStore.list_maps(workspace)
    _json_print(
        {
            "project": str(workspace.root),
            "count": len(maps),
            "maps": [map_id.cache_key for map_id in maps],
        }
    )
    return 0


def cmd_map_export(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    output = export_map_archive(workspace, map_id, args.output)
    _json_print(
        {
            "success": True,
            "map_id": map_id.cache_key,
            "archive": str(output),
        }
    )
    return 0


def cmd_map_validate(args: argparse.Namespace) -> int:
    result = validate_map_archive(args.archive)
    _json_print(result)
    return 0 if result["valid"] else 1


def cmd_map_pages(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        pages = [page.to_dict() for page in store.list_page_summaries()]
    finally:
        store.close()
    _json_print(
        {
            "map_id": map_id.cache_key,
            "page_count": len(pages),
            "pages": pages,
        }
    )
    return 0


def cmd_map_describe_page(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    if not any(
        (
            args.name,
            args.description,
            args.coverage_status,
            args.alias,
        )
    ):
        raise ValueError("Provide at least one page metadata update")
    store = MapStore.open(workspace, map_id, readonly=False)
    try:
        screen = store.get_screen(args.screen_id)
        if screen is None:
            matching = [
                page
                for page in store.list_page_summaries()
                if page.name == args.screen_id or args.screen_id in page.aliases
            ]
            if len(matching) != 1:
                raise ValueError(f"Unknown or ambiguous page: {args.screen_id}")
            args.screen_id = matching[0].screen_id
        updated = store.update_screen_card(
            args.screen_id,
            name=args.name,
            description=args.description,
            aliases=args.alias,
            coverage_status=args.coverage_status,
        )
        output = {
            "success": True,
            "map_id": map_id.cache_key,
            "page": {
                "screen_id": updated.screen_id,
                "name": updated.semantic_name,
                "description": updated.description,
                "coverage_status": updated.coverage_status,
                "aliases": list(updated.aliases),
            },
        }
    finally:
        store.close()
    _json_print(output)
    return 0


def cmd_map_build_paths(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    store = MapStore.open(workspace, map_id, readonly=False)
    try:
        created = store.materialize_paths()
        statistics = store.statistics()
    finally:
        store.close()
    _json_print(
        {
            "success": True,
            "map_id": map_id.cache_key,
            "paths_created": created,
            "statistics": statistics,
        }
    )
    return 0


def _resolve_page_id(store: MapStore, selector: str) -> str:
    exact = store.get_screen(selector)
    if exact is not None:
        return exact.screen_id
    matching = [
        page
 for page in store.list_page_summaries()
        if page.name == selector or selector in page.aliases
    ]
    if len(matching) != 1:
        raise ValueError(f"Unknown or ambiguous page: {selector}")
    return matching[0].screen_id


def _page_brief(store: MapStore, screen_id: str) -> dict[str, Any]:
    screen = store.get_screen(screen_id)
    if screen is None:
        raise ValueError(f"Unknown screen: {screen_id}")
    states = store.list_screen_states(screen_id=screen_id)
    return {
        "screen_id": screen.screen_id,
        "name": screen.semantic_name,
        "description": screen.description,
        "coverage_status": screen.coverage_status,
        "aliases": list(screen.aliases),
        "state_ids": [state.state_id for state in states],
    }


def _path_result(path: Any, service: MapQueryService) -> dict[str, Any] | None:
    if path is None:
        return None
    return {
        "start_state_id": path.start_state_id,
        "target_state_id": path.target_state_id,
        "step_count": len(path.steps),
        "reliability": path.reliability,
        "steps": [
            {
                "transition_id": step.transition.transition_id,
                "from_state_id": step.from_state_id,
                "to_state_id": step.to_state_id,
                "element_id": step.element_id,
                "action": step.action,
                "result_type": step.result_type,
                "reliability": step.reliability,
            }
            for step in path.steps
        ],
        "maestro_flow": service.generate_maestro_flow(path),
    }


def cmd_map_route(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        service = MapQueryService(store)
        target_screen_id = _resolve_page_id(store, args.to_page)
        if args.from_state is not None:
            if store.get_screen_state(args.from_state) is None:
                raise ValueError(f"Unknown start screen state: {args.from_state}")
            start_state_id = args.from_state
            from_screen_id = store.get_screen_state(args.from_state).screen_id
        elif args.from_page is not None:
            from_screen_id = _resolve_page_id(store, args.from_page)
            states = store.list_screen_states(screen_id=from_screen_id)
            if not states:
                raise ValueError(f"Page has no observed states: {args.from_page}")
            start_state_id = states[0].state_id
        else:
            entry = store.get_entry_state()
            if entry is None:
                raise ValueError("Map has no entry state; provide --from or --from-state")
            start_state_id = entry.state_id
            from_screen_id = entry.screen_id
        path = service.find_screen_path(
            target_screen_id,
            start_state_id=start_state_id,
        )
        output = {
            "success": path is not None,
            "map_id": map_id.cache_key,
            "from_page": _page_brief(store, from_screen_id),
            "to_page": _page_brief(store, target_screen_id),
            "reachable": path is not None,
            "path": _path_result(path, service),
        }
    finally:
        store.close()
    _json_print(output)
    return 0 if path is not None else 1


def cmd_map_pending(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        report = task_report(workspace, store)
    finally:
        store.close()
    for page in report["pages"]:
        tasks = page["tasks"]
        if args.limit >= 0 and len(tasks) > args.limit:
            page["tasks_truncated"] = len(tasks) - args.limit
            page["tasks"] = tasks[: args.limit]
        else:
            page["tasks_truncated"] = 0
    _json_print(
        {
            "success": True,
            "map_id": map_id.cache_key,
            **report,
        }
    )
    return 0


def cmd_map_current(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    config = load_project_config(workspace)
    if config is None:
        raise ValueError("Project is not initialized; run `aegis init` first")
    map_id = _map_id_from_args(args)
    result = identify_current_page(
        workspace=workspace,
        map_id=map_id,
        config=config,
    )
    _json_print(result)
    return 0 if result.get("success") else 1


def _element_result(value: Any, service: MapQueryService) -> dict[str, Any]:
    path = value.path
    return {
        "score": value.score,
        "matched_source": value.matched_source,
        "element": {
            "element_id": value.element.element_id,
            "semantic_name": value.element.semantic_name,
            "text": value.element.text,
            "resource_id": value.element.resource_id,
            "accessibility_id": value.element.accessibility_id,
            "content_desc": value.element.content_desc,
            "screen_id": value.element.screen_id,
            "state_id": value.element.state_id,
            "risk_level": value.element.risk_level,
            "is_actionable": value.element.is_actionable,
            "last_verified_at": value.element.last_verified_at,
            "locators": [
                {
                    "locator_id": locator.locator_id,
                    "strategy": locator.strategy,
                    "value": locator.value,
                    "priority": locator.priority,
                    "success_count": locator.success_count,
                    "attempt_count": locator.attempt_count,
                }
                for locator in value.locators
            ],
        },
        "screen": None
        if value.screen is None
        else {
            "screen_id": value.screen.screen_id,
            "semantic_name": value.screen.semantic_name,
            "description": value.screen.description,
            "coverage_status": value.screen.coverage_status,
        },
        "path": None
        if path is None
        else {
            "start_state_id": path.start_state_id,
            "target_state_id": path.target_state_id,
            "step_count": len(path.steps),
            "reliability": path.reliability,
            "steps": [
                {
                    "transition_id": step.transition.transition_id,
                    "from_state_id": step.from_state_id,
                    "to_state_id": step.to_state_id,
                    "element_id": step.element_id,
                    "action": step.action,
                    "result_type": step.result_type,
                    "reliability": step.reliability,
                }
                for step in path.steps
            ],
        },
        "maestro_flow": None
        if path is None
        else service.generate_maestro_flow(
            path,
            include_target_tap=False,
        ),
    }


def cmd_map_query(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    map_id = _map_id_from_args(args)
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        service = MapQueryService(store)
        results = service.locate_elements(args.query, limit=args.limit)
        if args.include_target_tap and results and results[0].path is not None:
            flow = service.generate_maestro_flow(
                results[0].path,
                include_target_tap=True,
            )
        else:
            flow = None
        output = {
            "query": args.query,
            "map_id": map_id.cache_key,
            "count": len(results),
            "results": [_element_result(value, service) for value in results],
        }
        if flow is not None:
            output["target_tap_flow"] = flow
    finally:
        store.close()
    _json_print(output)
    return 0


def cmd_explore_start(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    config = load_project_config(workspace)
    if config is None:
        raise ValueError("Project is not initialized; run `aegis init` first")
    map_id = resolve_map_id(config, _selection(args))
    run_id = _safe_run_id(args.run_id or _new_run_id())
    checkpoint_path = workspace.runs_dir / run_id / "checkpoint.json"
    if checkpoint_path.exists():
        raise FileExistsError(f"Run already exists: {run_id}")
    workspace.ensure_layout()
    _ensure_map(workspace, map_id)
    command = _worker_command(args, map_id, run_id)
    metadata = _write_run_metadata(
        workspace,
        run_id,
        map_id,
        command=command,
        foreground=args.foreground,
    )

    if args.foreground:
        result = run_exploration(
            workspace=workspace,
            map_id=map_id,
            run_id=run_id,
            config=config,
            max_actions=args.max_actions,
            chunk_steps=args.chunk_steps,
        )
        _update_run_metadata(
            workspace,
            run_id,
            {
                "status": "FINISHED",
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            },
        )
        output = {"success": result["status"] != "ERROR", **result, "metadata": metadata}
    else:
        output = _spawn_worker(workspace, args, map_id, run_id)
    _json_print(output)
    return 0 if output.get("success", True) else 1


def cmd_explore_resume(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    config = load_project_config(workspace)
    if config is None:
        raise ValueError("Project is not initialized; run `aegis init` first")
    run_id = _safe_run_id(args.run_id)
    metadata = _read_run_metadata(workspace, run_id)
    map_id = MapId.from_dict(metadata["map_id"])
    checkpoint = _checkpoint(workspace, run_id)
    if checkpoint is None:
        raise FileNotFoundError(f"Run has no checkpoint: {run_id}")
    pid = metadata.get("pid")
    process_active = _process_alive(int(pid)) if pid is not None else False
    if process_active:
        raise ValueError(f"Run worker is already active: {run_id}")

    if args.foreground:
        result = run_exploration(
            workspace=workspace,
            map_id=map_id,
            run_id=run_id,
            config=config,
            max_actions=args.max_actions,
            chunk_steps=args.chunk_steps,
        )
        _update_run_metadata(
            workspace,
            run_id,
            {"status": "FINISHED"},
        )
        output = {"success": result["status"] != "ERROR", **result}
    else:
        namespace = argparse.Namespace(
            project=args.project,
            map_selector=None,
            app_id=None,
            platform=None,
            app_version=None,
            build_number=None,
            locale=None,
            max_actions=args.max_actions,
            chunk_steps=args.chunk_steps,
        )
        output = _spawn_worker(workspace, namespace, map_id, run_id)
    _json_print(output)
    return 0 if output.get("success", True) else 1


def cmd_explore_status(args: argparse.Namespace) -> int:
    workspace = ProjectWorkspace(args.project)
    _json_print(_status_value(workspace, _safe_run_id(args.run_id)))
    return 0


def cmd_explore_signal(args: argparse.Namespace, status: str) -> int:
    workspace = ProjectWorkspace(args.project)
    run_id = _safe_run_id(args.run_id)
    metadata = _read_run_metadata(workspace, run_id)
    pid = metadata.get("pid")
    process_active = _process_alive(int(pid)) if pid is not None else False
    if not process_active:
        raise ValueError(f"Run worker is not active: {run_id}")
    os.kill(int(pid), signal.SIGTERM)
    _update_run_metadata(workspace, run_id, {"status": status})
    _json_print({"run_id": run_id, "pid": pid, "status": status})
    return 0


def cmd_explore_pause(args: argparse.Namespace) -> int:
    return cmd_explore_signal(args, "PAUSE_REQUESTED")


def cmd_explore_stop(args: argparse.Namespace) -> int:
    return cmd_explore_signal(args, "STOP_REQUESTED")


def cmd_explore_report(args: argparse.Namespace) -> int:
    cmd_explore_status(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the Aegis CLI parser."""

    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument("--version", action="version", version="aegis-cartographer 1.0.0")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize a project-local Aegis config")
    init.add_argument("--project", default=".")
    init.add_argument("--app-id", required=True)
    init.add_argument("--platform", required=True, choices=[item.value for item in Platform])
    init.add_argument("--app-version", required=True)
    init.add_argument("--build-number", required=True)
    init.add_argument("--locale", default="default")
    init.add_argument("--device-id", default="")
    init.add_argument(
        "--driver-type",
        choices=["maestro_mcp", "maestro_cli"],
        default="maestro_mcp",
    )
    init.add_argument("--maestro-path")
    init.add_argument("--adb-path")
    init.add_argument("--command-timeout", type=float, default=15.0)
    init.add_argument("--action-timeout", type=float, default=40.0)
    init.add_argument("--hierarchy-timeout", type=float, default=20.0)
    init.add_argument("--hierarchy-retries", type=int, default=3)
    init.add_argument("--hierarchy-retry-backoff-ms", type=int, default=500)
    init.add_argument("--animation-wait-timeout-ms", type=int, default=3000)
    init.add_argument("--stability-timeout", type=float, default=10.0)
    init.add_argument("--back-timeout", type=float, default=15.0)
    init.add_argument("--launch-timeout", type=float, default=30.0)
    init.add_argument("--max-actions", type=int, default=1000)
    init.add_argument("--max-depth", type=int, default=15)
    init.add_argument("--max-states", type=int, default=1000)
    init.add_argument("--max-errors", type=int, default=20)
    init.add_argument("--max-retries", type=int, default=2)
    init.add_argument("--max-restore-attempts", type=int, default=3)
    init.add_argument("--max-consecutive-noops", type=int, default=200)
    init.add_argument("--allow-input-text", action="store_true")
    init.add_argument("--blocked-keyword", action="append", default=[])
    init.add_argument("--needs-human-keyword", action="append", default=[])
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=cmd_init)

    maps = commands.add_parser("map", help="Manage project-local element maps")
    map_commands = maps.add_subparsers(dest="map_command", required=True)
    map_list = map_commands.add_parser("list", help="List project maps")
    _add_map_arguments(map_list, include_selector=False)
    map_list.set_defaults(handler=cmd_map_list)

    map_export = map_commands.add_parser("export", help="Export a portable map archive")
    _add_map_arguments(map_export)
    map_export.add_argument("--output")
    map_export.set_defaults(handler=cmd_map_export)

    map_validate = map_commands.add_parser("validate", help="Validate a map archive")
    map_validate.add_argument("--project", default=".")
    map_validate.add_argument("--archive", required=True)
    map_validate.set_defaults(handler=cmd_map_validate)

    map_pages = map_commands.add_parser("pages", help="List human-readable page cards")
    _add_map_arguments(map_pages)
    map_pages.set_defaults(handler=cmd_map_pages)

    map_describe = map_commands.add_parser(
        "describe-page",
        help="Update a page name, description, aliases, or coverage status",
    )
    _add_map_arguments(map_describe)
    map_describe.add_argument("screen_id")
    map_describe.add_argument("--name")
    map_describe.add_argument("--description")
    map_describe.add_argument("--alias", action="append")
    map_describe.add_argument(
        "--coverage-status",
        choices=[
            "DISCOVERED",
            "OBSERVED",
            "BASELINE_DONE",
            "DEEP_PENDING",
            "DEEP_DONE",
            "BLOCKED",
            "NEEDS_HUMAN",
            "OUT_OF_SCOPE",
            "EXTERNAL",
        ],
    )
    map_describe.set_defaults(handler=cmd_map_describe_page)

    map_build_paths = map_commands.add_parser(
        "build-paths",
        help="Backfill cached page paths from verified transitions",
    )
    _add_map_arguments(map_build_paths)
    map_build_paths.set_defaults(handler=cmd_map_build_paths)

    map_route = map_commands.add_parser(
        "route",
        help="Plan a route from one mapped page to another",
    )
    _add_map_arguments(map_route)
    map_route.add_argument(
        "--from",
        dest="from_page",
        default=None,
        help="Start page name, alias, or screen ID; defaults are not assumed",
    )
    map_route.add_argument(
        "--from-state",
        dest="from_state",
        help="Explicit start screen state ID",
    )
    map_route.add_argument("--to", dest="to_page", required=True)
    map_route.set_defaults(handler=cmd_map_route)

    map_pending = map_commands.add_parser(
        "pending",
        help="Show unfinished tasks grouped by Chinese page cards",
    )
    _add_map_arguments(map_pending)
    map_pending.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum tasks shown per page; use -1 for all",
    )
    map_pending.set_defaults(handler=cmd_map_pending)

    map_current = map_commands.add_parser(
        "current",
        help="Identify the currently visible mapped page",
    )
    _add_map_arguments(map_current)
    map_current.set_defaults(handler=cmd_map_current)

    map_query = map_commands.add_parser("query", help="Query elements and paths")
    _add_map_arguments(map_query)
    map_query.add_argument("query")
    map_query.add_argument("--limit", type=int, default=10)
    map_query.add_argument("--include-target-tap", action="store_true")
    map_query.set_defaults(handler=cmd_map_query)

    explore = commands.add_parser("explore", help="Control exploration runs")
    explore_commands = explore.add_subparsers(dest="explore_command", required=True)

    explore_start = explore_commands.add_parser("start", help="Start a new exploration run")
    _add_map_arguments(explore_start)
    explore_start.add_argument("--run-id")
    explore_start.add_argument("--foreground", action="store_true")
    explore_start.add_argument("--max-actions", type=int)
    explore_start.add_argument("--chunk-steps", type=int, default=10)
    explore_start.set_defaults(handler=cmd_explore_start)

    explore_resume = explore_commands.add_parser("resume", help="Resume a paused run")
    explore_resume.add_argument("--project", default=".")
    explore_resume.add_argument("--run-id", required=True)
    explore_resume.add_argument("--foreground", action="store_true")
    explore_resume.add_argument("--max-actions", type=int)
    explore_resume.add_argument("--chunk-steps", type=int, default=10)
    explore_resume.set_defaults(handler=cmd_explore_resume)

    explore_status = explore_commands.add_parser("status", help="Show run status")
    explore_status.add_argument("--project", default=".")
    explore_status.add_argument("--run-id", required=True)
    explore_status.set_defaults(handler=cmd_explore_status)

    for name, handler, help_text in (
        ("pause", cmd_explore_pause, "Request a graceful pause"),
        ("stop", cmd_explore_stop, "Request a graceful stop"),
        ("report", cmd_explore_report, "Alias for exploration status"),
    ):
        command_parser = explore_commands.add_parser(name, help=help_text)
        command_parser.add_argument("--project", default=".")
        command_parser.add_argument("--run-id", required=True)
        command_parser.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Aegis CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as error:
        _json_print({"success": False, "error": type(error).__name__, "message": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
