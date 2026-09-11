"""Project-local Aegis configuration."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from aegis_cartographer.core.models import Platform
from aegis_cartographer.core.storage.models import MapId
from aegis_cartographer.core.storage.workspace import ProjectWorkspace

CONFIG_SCHEMA_VERSION = 1


@dataclass
class MaestroConfig:
    """Device execution defaults."""

    device_id: str = ""
    driver_type: str = "maestro_mcp"
    command_timeout: float = 15.0
    maestro_path: str | None = None
    adb_path: str | None = None
    action_timeout: float = 40.0
    hierarchy_timeout: float = 20.0
    hierarchy_retries: int = 3
    hierarchy_retry_backoff_ms: int = 500
    animation_wait_timeout_ms: int = 3000
    stability_timeout: float = 10.0
    back_timeout: float = 15.0
    launch_timeout: float = 30.0
    mcp_no_viewer: bool = True
    mcp_start_timeout: float = 10.0


@dataclass
class ExplorationConfig:
    """Default exploration budget."""

    max_actions: int = 1000
    max_depth: int = 15
    max_states: int = 1000
    max_errors: int = 20
    max_retries: int = 2
    max_restore_attempts: int = 3
    max_consecutive_noops: int = 200


@dataclass
class SafetyConfig:
    """Project safety defaults."""

    allow_input_text: bool = False
    blocked_keywords: list[str] = field(default_factory=list)
    needs_human_keywords: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    """Configuration stored in a business project's ``.aegis/config.json``."""

    schema_version: int = CONFIG_SCHEMA_VERSION
    app_id: str = ""
    platform: Platform = Platform.ANDROID
    app_version: str = ""
    build_number: str = ""
    locale: str = "default"
    maestro: MaestroConfig = field(default_factory=MaestroConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the project configuration."""

        value = asdict(self)
        value["platform"] = self.platform.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProjectConfig:
        """Deserialize and validate project configuration."""

        if not isinstance(value, Mapping):
            raise ValueError("Project config must be a JSON object")
        schema_version = int(value.get("schema_version", 0))
        if schema_version != CONFIG_SCHEMA_VERSION:
            raise ValueError(f"Unsupported project config schema: {schema_version}")

        maestro_value = value.get("maestro", {})
        exploration_value = value.get("exploration", {})
        safety_value = value.get("safety", {})
        if not isinstance(maestro_value, Mapping):
            raise ValueError("maestro config must be an object")
        if not isinstance(exploration_value, Mapping):
            raise ValueError("exploration config must be an object")
        if not isinstance(safety_value, Mapping):
            raise ValueError("safety config must be an object")

        platform_raw = str(value.get("platform", "android")).lower()
        try:
            platform = Platform(platform_raw)
        except ValueError as error:
            raise ValueError(f"Unsupported platform: {platform_raw}") from error

        config = cls(
            app_id=str(value.get("app_id", "")),
            platform=platform,
            app_version=str(value.get("app_version", "")),
            build_number=str(value.get("build_number", "")),
            locale=str(value.get("locale", "default")),
            maestro=MaestroConfig(
                device_id=str(maestro_value.get("device_id", "")),
                driver_type=str(
                    maestro_value.get("driver_type", "maestro_mcp")
                ),
                command_timeout=float(maestro_value.get("command_timeout", 15.0)),
                maestro_path=(
                    None
                    if maestro_value.get("maestro_path") is None
                    else str(maestro_value.get("maestro_path"))
                ),
                adb_path=(
                    None
                    if maestro_value.get("adb_path") is None
                    else str(maestro_value.get("adb_path"))
                ),
                action_timeout=float(maestro_value.get("action_timeout", 40.0)),
                hierarchy_timeout=float(maestro_value.get("hierarchy_timeout", 20.0)),
                hierarchy_retries=int(maestro_value.get("hierarchy_retries", 3)),
                hierarchy_retry_backoff_ms=int(
                    maestro_value.get("hierarchy_retry_backoff_ms", 500)
                ),
                animation_wait_timeout_ms=int(
                    maestro_value.get("animation_wait_timeout_ms", 3000)
                ),
                stability_timeout=float(maestro_value.get("stability_timeout", 10.0)),
                back_timeout=float(maestro_value.get("back_timeout", 15.0)),
                launch_timeout=float(maestro_value.get("launch_timeout", 30.0)),
                mcp_no_viewer=bool(maestro_value.get("mcp_no_viewer", True)),
                mcp_start_timeout=float(
                    maestro_value.get("mcp_start_timeout", 10.0)
                ),
            ),
            exploration=ExplorationConfig(
                max_actions=int(exploration_value.get("max_actions", 1000)),
                max_depth=int(exploration_value.get("max_depth", 15)),
                max_states=int(exploration_value.get("max_states", 1000)),
                max_errors=int(exploration_value.get("max_errors", 20)),
                max_retries=int(exploration_value.get("max_retries", 2)),
                max_restore_attempts=int(
                    exploration_value.get("max_restore_attempts", 3)
                ),
                max_consecutive_noops=int(
                    exploration_value.get("max_consecutive_noops", 200)
                ),
            ),
            safety=SafetyConfig(
                allow_input_text=bool(safety_value.get("allow_input_text", False)),
                blocked_keywords=[str(item) for item in safety_value.get("blocked_keywords", ())],
                needs_human_keywords=[
                    str(item) for item in safety_value.get("needs_human_keywords", ())
                ],
            ),
        )
        config.validate(allow_partial=True)
        return config

    def validate(self, *, allow_partial: bool = False) -> None:
        """Validate identity and execution limits."""

        required = ("app_id", "app_version", "build_number")
        if not allow_partial:
            missing = [name for name in required if not getattr(self, name).strip()]
            if missing:
                raise ValueError(f"Project config is missing: {', '.join(missing)}")
        if self.maestro.driver_type not in {"maestro_cli", "maestro_mcp"}:
            raise ValueError(
                "maestro.driver_type must be 'maestro_cli' or 'maestro_mcp'"
            )
        duration_fields = {
            "command_timeout": self.maestro.command_timeout,
            "action_timeout": self.maestro.action_timeout,
            "hierarchy_timeout": self.maestro.hierarchy_timeout,
            "stability_timeout": self.maestro.stability_timeout,
            "back_timeout": self.maestro.back_timeout,
            "launch_timeout": self.maestro.launch_timeout,
        }
        for name, value in duration_fields.items():
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"maestro.{name} must be a positive finite number")
        positive_integers = {
            "hierarchy_retries": self.maestro.hierarchy_retries,
            "animation_wait_timeout_ms": self.maestro.animation_wait_timeout_ms,
        }
        for name, value in positive_integers.items():
            if value < 1:
                raise ValueError(f"maestro.{name} must be at least 1")
        if self.maestro.hierarchy_retry_backoff_ms < 0:
            raise ValueError("maestro.hierarchy_retry_backoff_ms cannot be negative")
        limits = {
            "max_actions": self.exploration.max_actions,
            "max_depth": self.exploration.max_depth,
            "max_states": self.exploration.max_states,
            "max_errors": self.exploration.max_errors,
            "max_retries": self.exploration.max_retries,
            "max_restore_attempts": self.exploration.max_restore_attempts,
            "max_consecutive_noops": self.exploration.max_consecutive_noops,
        }
        for name, limit in limits.items():
            if limit < 1:
                raise ValueError(f"exploration.{name} must be at least 1")
    def default_map_id(self) -> MapId:
        """Return the default map identity for this project."""

        self.validate()
        return MapId(
            app_id=self.app_id,
            platform=self.platform,
            app_version=self.app_version,
            build_number=self.build_number,
            locale=self.locale,
        )


def load_project_config(workspace: ProjectWorkspace) -> ProjectConfig | None:
    """Load a project config, returning ``None`` when it does not exist."""

    if not workspace.config_path.is_file():
        return None
    try:
        value = workspace.read_json(workspace.config_path)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid project config JSON: {error}") from error
    return ProjectConfig.from_dict(value)


def save_project_config(workspace: ProjectWorkspace, config: ProjectConfig) -> Path:
    """Atomically save a project config."""

    config.validate()
    workspace.ensure_layout()
    workspace.write_json_atomic(workspace.config_path, config.to_dict())
    return workspace.config_path
