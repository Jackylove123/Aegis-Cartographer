"""Typed models for project-local Aegis map artifacts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from aegis_cartographer.core.models import Platform, ScreenSignature, ScreenStateType

SCHEMA_VERSION = 1
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_UNSAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._+-]")


class ExplorationAction(str, Enum):
    """Actions supported by the exploration engine."""

    TAP = "TAP"
    LONG_PRESS = "LONG_PRESS"
    SWIPE = "SWIPE"
    SCROLL = "SCROLL"
    BACK = "BACK"
    CLOSE_OVERLAY = "CLOSE_OVERLAY"
    INPUT_TEXT = "INPUT_TEXT"


class ActionResultType(str, Enum):
    """Classification of a state transition after executing an action."""

    NEW_PAGE = "NEW_PAGE"
    OVERLAY = "OVERLAY"
    STATE_CHANGE = "STATE_CHANGE"
    NO_OP = "NO_OP"
    EXTERNAL_APP = "EXTERNAL_APP"
    APP_EXIT = "APP_EXIT"
    CRASH = "CRASH"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class MapId:
    """Stable identity of one isolated map artifact."""

    app_id: str
    platform: Platform
    app_version: str
    build_number: str
    locale: str = "default"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in ("app_id", "app_version", "build_number", "locale"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"MapId.{field} must be a non-empty string")
            if Path(value).name in {"", ".", ".."} or "/" in value or "\\" in value:
                raise ValueError(f"MapId.{field} must not contain path separators")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported map schema version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete map identity."""

        return {
            "app_id": self.app_id,
            "platform": self.platform.value,
            "app_version": self.app_version,
            "build_number": self.build_number,
            "locale": self.locale,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MapId:
        """Deserialize and validate a map identity."""

        if not isinstance(value, Mapping):
            raise ValueError("MapId must be a JSON object")
        required = {"app_id", "platform", "app_version", "build_number"}
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"MapId is missing fields: {', '.join(missing)}")
        try:
            platform = Platform(str(value["platform"]).lower())
        except ValueError as error:
            raise ValueError(f"Unsupported map platform: {value['platform']}") from error
        return cls(
            app_id=str(value["app_id"]),
            platform=platform,
            app_version=str(value["app_version"]),
            build_number=str(value["build_number"]),
            locale=str(value.get("locale", "default")),
            schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
        )

    @staticmethod
    def _safe_component(value: str) -> str:
        normalized = value.strip()
        if _SAFE_COMPONENT_RE.fullmatch(normalized) and normalized not in {".", ".."}:
            return normalized
        slug = _UNSAFE_COMPONENT_RE.sub("_", normalized) or "item"
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        return f"{slug[:48]}--{digest}"

    @property
    def relative_directory(self) -> Path:
        """Return the map directory relative to a project's `.aegis/maps` root."""

        return Path(
            self._safe_component(self.app_id),
            self.platform.value,
            f"{self._safe_component(self.app_version)}+{self._safe_component(self.build_number)}",
            self._safe_component(self.locale),
        )

    @property
    def cache_key(self) -> str:
        """Return a stable string identity that is safe for logs."""

        return (
            f"{self.app_id}@{self.platform.value}:{self.app_version}"
            f"+{self.build_number}/{self.locale}"
        )


@dataclass(frozen=True)
class MapManifest:
    """Self-describing metadata for a map artifact directory."""

    map_id: MapId
    created_at: str
    updated_at: str
    generator: str = "aegis-cartographer"
    database_file: str = "map.sqlite"
    artifact_type: str = "aegis-map"
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest."""

        result = asdict(self)
        result["map_id"] = self.map_id.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MapManifest:
        """Deserialize and validate a manifest."""

        if not isinstance(value, Mapping):
            raise ValueError("Map manifest must be a JSON object")
        artifact_type = str(value.get("artifact_type", ""))
        if artifact_type != "aegis-map":
            raise ValueError(f"Not an Aegis map artifact: {artifact_type!r}")
        map_id = MapId.from_dict(value.get("map_id", {}))
        schema_version = int(value.get("schema_version", 0))
        if schema_version != map_id.schema_version:
            raise ValueError("Manifest schema_version does not match map_id")
        return cls(
            map_id=map_id,
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            generator=str(value.get("generator", "aegis-cartographer")),
            database_file=str(value.get("database_file", "map.sqlite")),
            artifact_type=artifact_type,
            schema_version=schema_version,
        )


@dataclass(frozen=True)
class ElementLocator:
    """One executable strategy for finding an element."""

    platform: Platform
    strategy: str
    value: str
    priority: int = 100
    scope: str = "screen_state"
    success_count: int = 0
    attempt_count: int = 0
    last_error: str | None = None
    last_verified_at: str | None = None
    locator_id: str | None = None

    def __post_init__(self) -> None:
        if not self.strategy.strip() or not self.value.strip():
            raise ValueError("Locator strategy and value must be non-empty")
        if self.priority < 1:
            raise ValueError("Locator priority must be positive")
        if self.success_count < 0 or self.attempt_count < 0:
            raise ValueError("Locator counters cannot be negative")
        if self.attempt_count < self.success_count:
            raise ValueError("Locator attempt_count cannot be less than success_count")


@dataclass(frozen=True)
class ElementRecord:
    """Persistent, queryable element record."""

    element_id: str
    element_key: str
    screen_id: str
    state_id: str
    semantic_name: str | None
    aliases: tuple[str, ...]
    canonical_element_id: str | None
    role: str
    is_actionable: bool
    is_dynamic: bool
    risk_level: str
    actions: tuple[ExplorationAction, ...]
    preconditions: tuple[str, ...]
    text: str
    resource_id: str
    accessibility_id: str
    content_desc: str
    class_name: str
    bounds: tuple[int, int, int, int]
    occurrence_index: int
    selector: str
    confidence: float
    first_seen_at: str
    last_verified_at: str | None
    locators: tuple[ElementLocator, ...]


@dataclass(frozen=True)
class ScreenRecord:
    """A persistent logical screen record."""

    screen_id: str
    semantic_name: str
    screen_type: str
    business_domain: str | None
    aliases: tuple[str, ...]
    confidence: float
    created_at: str
    updated_at: str
    description: str = ""
    coverage_status: str = "DISCOVERED"


@dataclass(frozen=True)
class PageSummary:
    """A human-readable card for one logical page."""

    screen_id: str
    name: str
    description: str
    screen_type: str
    business_domain: str | None
    coverage_status: str
    aliases: tuple[str, ...]
    state_count: int
    element_count: int
    incoming_transition_count: int
    outgoing_transition_count: int
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize a page card for CLI, MCP, and portable exports."""

        return {
            "screen_id": self.screen_id,
            "name": self.name,
            "description": self.description,
            "screen_type": self.screen_type,
            "business_domain": self.business_domain,
            "coverage_status": self.coverage_status,
            "aliases": list(self.aliases),
            "state_count": self.state_count,
            "element_count": self.element_count,
            "incoming_transitions": self.incoming_transition_count,
            "outgoing_transitions": self.outgoing_transition_count,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TransitionRecord:
    """A directed, action-triggered transition between screen states."""

    transition_id: str
    from_state_id: str
    element_id: str | None
    action: ExplorationAction
    result_type: ActionResultType
    to_state_id: str | None
    maestro_commands: tuple[Mapping[str, Any], ...]
    restore_strategy: tuple[Mapping[str, Any], ...]
    observed_count: int
    success_count: int
    last_verified_at: str | None


@dataclass(frozen=True)
class ObservationRecord:
    """Metadata for one immutable screen observation."""

    observation_id: str
    run_id: str
    state_id: str
    observed_at: str
    structural_hash: str
    interactive_hash: str
    landmark_hash: str
    state_type: ScreenStateType
    package_name: str
    device_id: str
    screenshot_path: str | None
    account_state: str
    network_state: str
    trigger_transition_id: str | None
    signature: ScreenSignature


@dataclass(frozen=True)
class ElementSearchResult:
    """Search hit returned to an AI test client."""

    element: ElementRecord
    score: float
    matched_source: str


@dataclass(frozen=True)
class ScreenSearchResult:
    """Search hit for a logical screen and its known states."""

    screen_id: str
    semantic_name: str
    score: float
    matched_source: str
    state_ids: tuple[str, ...]
