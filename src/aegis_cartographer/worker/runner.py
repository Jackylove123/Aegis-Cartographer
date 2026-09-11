"""Runnable exploration worker implementation."""

from __future__ import annotations

import signal
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from aegis_cartographer.core.exploration import (
    ExplorationBudget,
    ExplorationEngine,
    ExplorationStatus,
    SafetyPolicy,
)
from aegis_cartographer.core.storage import MapId, MapStore, ProjectWorkspace
from aegis_cartographer.device.maestro_driver import MaestroDriver
from aegis_cartographer.device.maestro_mcp_driver import MaestroMcpDriver
from aegis_cartographer.worker.config import ProjectConfig

DriverFactory = Callable[..., MaestroDriver]


def _budget_from_config(config: ProjectConfig) -> ExplorationBudget:
    values = config.exploration
    return ExplorationBudget(
        max_actions=values.max_actions,
        max_depth=values.max_depth,
        max_states=values.max_states,
        max_errors=values.max_errors,
        max_retries=values.max_retries,
        max_restore_attempts=values.max_restore_attempts,
        max_consecutive_noops=values.max_consecutive_noops,
    )


def _safety_from_config(config: ProjectConfig) -> SafetyPolicy:
    defaults = SafetyPolicy()
    return SafetyPolicy(
        blocked_keywords=defaults.blocked_keywords + tuple(config.safety.blocked_keywords),
        needs_human_keywords=defaults.needs_human_keywords
        + tuple(config.safety.needs_human_keywords),
        allow_input_text=config.safety.allow_input_text,
    )


def _write_initial_metadata(
    workspace: ProjectWorkspace,
    run_id: str,
    map_id: MapId,
) -> None:
    metadata_path = workspace.runs_dir / run_id / "run.json"
    if metadata_path.exists():
        return
    metadata_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.write_json_atomic(
        metadata_path,
        {
            "run_id": run_id,
            "map_id": map_id.to_dict(),
            "map_key": map_id.cache_key,
            "command": ["direct-api"],
            "foreground": True,
            "pid": None,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "status": "RUNNING",
        },
    )


def _finish_metadata(workspace: ProjectWorkspace, run_id: str, status: str) -> None:
    metadata_path = workspace.runs_dir / run_id / "run.json"
    if not metadata_path.exists():
        return
    value = workspace.read_json(metadata_path)
    value.update(
        {
            "status": "FINISHED",
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "final_status": status,
        }
    )
    workspace.write_json_atomic(metadata_path, value)


def run_exploration(
    *,
    workspace: ProjectWorkspace,
    map_id: MapId,
    run_id: str,
    config: ProjectConfig,
    max_actions: int | None = None,
    chunk_steps: int = 10,
    driver_factory: DriverFactory | None = None,
) -> dict[str, Any]:
    """Run or resume one exploration run in bounded checkpoint chunks."""

    if chunk_steps < 1:
        raise ValueError("chunk_steps must be at least 1")
    budget = _budget_from_config(config)
    if max_actions is not None:
        if max_actions < 1:
            raise ValueError("max_actions must be at least 1")
        budget = ExplorationBudget(
            max_actions=min(max_actions, budget.max_actions),
            max_depth=budget.max_depth,
            max_states=budget.max_states,
            max_errors=budget.max_errors,
            max_retries=budget.max_retries,
            max_restore_attempts=budget.max_restore_attempts,
            max_consecutive_noops=budget.max_consecutive_noops,
        )

    if driver_factory is not None:
        factory = driver_factory
    elif config.maestro.driver_type == "maestro_cli":
        factory = MaestroDriver
    else:
        factory = MaestroMcpDriver
    driver_arguments: dict[str, Any] = {
        "device_id": config.maestro.device_id,
        "app_id": map_id.app_id,
        "maestro_path": config.maestro.maestro_path,
        "adb_path": config.maestro.adb_path,
        "command_timeout": config.maestro.command_timeout,
        "action_timeout": config.maestro.action_timeout,
        "hierarchy_timeout": config.maestro.hierarchy_timeout,
        "hierarchy_retries": config.maestro.hierarchy_retries,
        "hierarchy_retry_backoff_ms": config.maestro.hierarchy_retry_backoff_ms,
        "animation_wait_timeout_ms": config.maestro.animation_wait_timeout_ms,
        "stability_timeout": config.maestro.stability_timeout,
        "back_timeout": config.maestro.back_timeout,
        "launch_timeout": config.maestro.launch_timeout,
    }
    if factory is MaestroMcpDriver:
        driver_arguments.update(
            {
                "mcp_no_viewer": config.maestro.mcp_no_viewer,
                "mcp_start_timeout": config.maestro.mcp_start_timeout,
            }
        )
    driver = factory(**driver_arguments)
    if hasattr(driver, "connect"):
        driver.connect()

    stop_requested = False

    def request_stop(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    store: MapStore | None = None
    driver_closed = False
    try:
        workspace.ensure_layout()
        _write_initial_metadata(workspace, run_id, map_id)
        store = MapStore.open(workspace, map_id, readonly=False)
        engine = ExplorationEngine(
            store=store,
            driver=driver,
            run_id=run_id,
            target_app_id=map_id.app_id,
            device_id=config.maestro.device_id,
            budget=budget,
            action_timeout=config.maestro.action_timeout,
            back_timeout=config.maestro.back_timeout,
            launch_timeout=config.maestro.launch_timeout,
            stability_timeout=config.maestro.stability_timeout,
            safety_policy=_safety_from_config(config),
        )
        remaining = budget.max_actions - engine.checkpoint.actions_executed
        while remaining > 0 and not stop_requested:
            if engine.checkpoint.status not in {
                ExplorationStatus.CREATED,
                ExplorationStatus.RUNNING,
                ExplorationStatus.PAUSED,
            }:
                break
            checkpoint = engine.run(max_steps=min(chunk_steps, remaining))
            remaining = budget.max_actions - checkpoint.actions_executed
            if checkpoint.status == ExplorationStatus.PAUSED:
                continue
            if checkpoint.status != ExplorationStatus.RUNNING:
                break

        if stop_requested:
            engine.pause()
        result = {
            "run_id": run_id,
            "map_id": map_id.cache_key,
            "status": engine.checkpoint.status.value,
            "actions_executed": engine.checkpoint.actions_executed,
            "states_created": engine.checkpoint.states_created,
            "errors": engine.checkpoint.errors,
            "last_error": engine.checkpoint.last_error,
            "coverage": engine.coverage_report(),
        }
        engine.close()
        result["paths_materialized"] = store.materialize_paths()
        _finish_metadata(workspace, run_id, result["status"])
    finally:
        if not driver_closed and hasattr(driver, "close"):
            driver.close()
            driver_closed = True
        if store is not None:
            store.close()
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    return result
