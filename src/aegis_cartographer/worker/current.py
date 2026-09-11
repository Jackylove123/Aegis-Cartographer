"""Identify the currently visible mapped page without mutating the map."""

from __future__ import annotations

from typing import Any, Callable

from aegis_cartographer.core.identity import ScreenMatcher, observe_screen
from aegis_cartographer.core.storage.models import MapId
from aegis_cartographer.core.storage.store import MapStore
from aegis_cartographer.core.storage.workspace import ProjectWorkspace
from aegis_cartographer.device.maestro_driver import MaestroDriver
from aegis_cartographer.worker.config import ProjectConfig

DriverFactory = Callable[..., MaestroDriver]


def identify_current_page(
    *,
    workspace: ProjectWorkspace,
    map_id: MapId,
    config: ProjectConfig,
    driver_factory: DriverFactory | None = None,
) -> dict[str, Any]:
    """Read one hierarchy and match it against the project's known pages."""

    factory = driver_factory or MaestroDriver
    driver = factory(
        device_id=config.maestro.device_id,
        app_id=map_id.app_id,
        maestro_path=config.maestro.maestro_path,
        adb_path=config.maestro.adb_path,
        command_timeout=config.maestro.command_timeout,
        action_timeout=config.maestro.action_timeout,
        hierarchy_timeout=config.maestro.hierarchy_timeout,
        hierarchy_retries=config.maestro.hierarchy_retries,
        hierarchy_retry_backoff_ms=config.maestro.hierarchy_retry_backoff_ms,
        animation_wait_timeout_ms=config.maestro.animation_wait_timeout_ms,
        stability_timeout=config.maestro.stability_timeout,
        back_timeout=config.maestro.back_timeout,
        launch_timeout=config.maestro.launch_timeout,
    )
    store = MapStore.open(workspace, map_id, readonly=True)
    try:
        foreground_reader = getattr(driver, "get_foreground_activity", None)
        foreground = (
            foreground_reader()
            if callable(foreground_reader)
            else None
        )
        if (
            foreground is not None
            and foreground.success
            and foreground.package_name != map_id.app_id
        ):
            return {
                "success": False,
                "map_id": map_id.cache_key,
                "error": "current foreground app differs from target app",
                "foreground": foreground.to_dict(),
            }
        stable = driver.wait_until_stable(timeout=config.maestro.stability_timeout)
        hierarchy = stable.get("hierarchy") or driver.get_hierarchy()
        if not isinstance(hierarchy, dict) or not hierarchy:
            return {
                "success": False,
                "error": "device returned an empty hierarchy",
                "hierarchy_error": stable.get("last_hierarchy_error"),
            }
        observation = observe_screen(
            hierarchy,
            target_app_id=map_id.app_id,
            package_name=(
                foreground.package_name
                if foreground is not None and foreground.success
                else None
            ),
        )
        match = ScreenMatcher().match(
            observation,
            store.list_screen_states(),
        )
        state = (
            store.get_screen_state(match.matched_state_id)
            if match.matched_state_id is not None
            else store.get_screen_state(match.recommended_state_id)
        )
        screen = None if state is None else store.get_screen(state.screen_id)
        return {
            "success": state is not None and screen is not None,
            "map_id": map_id.cache_key,
            "foreground": None if foreground is None else foreground.to_dict(),
            "package_name": observation.package_name,
            "state_type": observation.state_type.value,
            "landmarks": list(observation.landmarks),
            "match_type": match.match_type.value,
            "confidence": match.confidence,
            "reason": match.reason,
            "state_id": None if state is None else state.state_id,
            "page": None if screen is None else {
                "screen_id": screen.screen_id,
                "name": screen.semantic_name,
                "description": screen.description,
                "coverage_status": screen.coverage_status,
                "aliases": list(screen.aliases),
            },
            "recommended_state_id": match.recommended_state_id,
        }
    finally:
        store.close()
