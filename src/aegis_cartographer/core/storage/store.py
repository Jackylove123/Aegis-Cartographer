"""SQLite-backed, project-local Aegis map store."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from aegis_cartographer.core.models import (
    Platform,
    ScreenObservation,
    ScreenSignature,
    ScreenStateRecord,
    ScreenStateType,
    UiElement,
)
from aegis_cartographer.core.storage.models import (
    SCHEMA_VERSION,
    ActionResultType,
    ElementLocator,
    ElementRecord,
    ElementSearchResult,
    ExplorationAction,
    MapId,
    MapManifest,
    ObservationRecord,
    PageSummary,
    ScreenRecord,
    ScreenSearchResult,
    TransitionRecord,
)
from aegis_cartographer.core.storage.schema import SCHEMA_SQL
from aegis_cartographer.core.storage.workspace import ProjectWorkspace


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Sequence[Any]) -> str:
    serialized = _json(list(value))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _bool(value: Any) -> bool:
    return bool(value)


class _MapWriteLock:
    """Advisory exclusive lock for one map's writer."""

    def __init__(self, workspace: ProjectWorkspace, map_id: MapId) -> None:
        lock_name = _digest(
            (
                map_id.app_id,
                map_id.platform.value,
                map_id.app_version,
                map_id.build_number,
                map_id.locale,
                map_id.schema_version,
            )
        )
        self.path = workspace.runs_dir / "locks" / f"{lock_name}.lock"
        self._stream = None

    def __enter__(self) -> _MapWriteLock:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._stream = self.path.open("a+", encoding="utf-8")
        os.fchmod(self._stream.fileno(), 0o600)
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


class MapStore:
    """Transactional SQLite store for one isolated map artifact."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        map_id: MapId,
        *,
        readonly: bool = False,
        initialize: bool = False,
    ) -> None:
        self.workspace = workspace
        self.map_id = map_id
        self.readonly = readonly
        self.map_directory = workspace.map_directory(map_id)
        self.database_path = self.map_directory / "map.sqlite"
        self.manifest_path = self.map_directory / "manifest.json"
        self._lock: _MapWriteLock | None = None
        initialization_complete = False

        try:
            if initialize:
                if self.readonly:
                    raise ValueError("A readonly store cannot initialize a map")
                workspace.ensure_layout()
                self.map_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
                self._lock = _MapWriteLock(workspace, map_id)
                if self.manifest_path.exists() or self.database_path.exists():
                    raise FileExistsError(f"Map already exists: {map_id.cache_key}")
            else:
                if not self.manifest_path.is_file():
                    raise FileNotFoundError(f"Map manifest not found: {self.manifest_path}")
                if not self.readonly:
                    workspace.ensure_layout()
                    self._lock = _MapWriteLock(workspace, map_id)

            self.connection = self._connect(readonly=readonly)
            if initialize:
                now = _utc_now()
                self.connection.executescript(SCHEMA_SQL)
                with self.connection:
                    for key, value in (
                        ("schema_version", SCHEMA_VERSION),
                        ("map_id", _json(map_id.to_dict())),
                        ("created_at", now),
                        ("updated_at", now),
                    ):
                        self.connection.execute(
                            "INSERT INTO map_metadata(key, value) VALUES (?, ?)",
                            (key, str(value)),
                        )
                self.manifest = MapManifest(
                    map_id=map_id,
                    created_at=now,
                    updated_at=now,
                )
                workspace.write_json_atomic(self.manifest_path, self.manifest.to_dict())
                initialization_complete = True
            else:
                if not readonly:
                    self._ensure_additive_screen_columns()
                self.manifest = self._load_manifest()
                if self.manifest.map_id != map_id:
                    raise ValueError("Manifest map identity does not match requested MapId")
                self._verify_database_identity()
        except BaseException:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
            if self._lock is not None:
                self._lock.__exit__(None, None, None)
                self._lock = None
            if initialize and not initialization_complete:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    try:
                        os.unlink(str(self.database_path) + suffix)
                    except FileNotFoundError:
                        pass
                try:
                    self.map_directory.rmdir()
                except OSError:
                    pass
            raise

    @classmethod
    def create(cls, workspace: ProjectWorkspace, map_id: MapId) -> MapStore:
        """Create a new isolated map without overwriting an existing artifact."""

        return cls(workspace, map_id, readonly=False, initialize=True)

    @classmethod
    def open(
        cls,
        workspace: ProjectWorkspace,
        map_id: MapId,
        *,
        readonly: bool = True,
    ) -> MapStore:
        """Open an existing map, read-only by default."""

        return cls(workspace, map_id, readonly=readonly, initialize=False)

    @staticmethod
    def list_maps(workspace: ProjectWorkspace) -> list[MapId]:
        """List valid project-local maps."""

        workspace.ensure_layout()
        maps: list[MapId] = []
        for manifest_path in sorted(workspace.maps_dir.glob("*/*/*/*/manifest.json")):
            if manifest_path.is_symlink():
                raise ValueError(f"Map manifest must not be a symlink: {manifest_path}")
            manifest = MapManifest.from_dict(workspace.read_json(manifest_path))
            expected = workspace.map_directory(manifest.map_id) / "manifest.json"
            if manifest_path.resolve() != expected.resolve():
                raise ValueError(f"Map identity/path mismatch: {manifest_path}")
            maps.append(manifest.map_id)
        return maps

    def _connect(self, *, readonly: bool) -> sqlite3.Connection:
        if self.database_path.is_symlink():
            raise ValueError("Map database must not be a symlink")
        if readonly:
            uri = self.database_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30.0)
            connection.execute("PRAGMA query_only = ON")
        else:
            connection = sqlite3.connect(self.database_path, timeout=30.0)
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            os.chmod(self.database_path, 0o600)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.row_factory = sqlite3.Row
        return connection

    def _load_manifest(self) -> MapManifest:
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Map manifest not found: {self.manifest_path}")
        if self.manifest_path.is_symlink():
            raise ValueError("Map manifest must not be a symlink")
        manifest = MapManifest.from_dict(self.workspace.read_json(self.manifest_path))
        if manifest.database_file != "map.sqlite":
            raise ValueError("Map manifest database must be map.sqlite")
        return manifest

    def _verify_database_identity(self) -> None:
        version = int(
            self.connection.execute("PRAGMA user_version").fetchone()["user_version"]
        )
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported database schema version: {version}")
        schema_row = self.connection.execute(
            "SELECT value FROM map_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if schema_row is None or int(schema_row["value"]) != SCHEMA_VERSION:
            raise ValueError("Database metadata schema version is invalid")
        row = self.connection.execute(
            "SELECT value FROM map_metadata WHERE key = 'map_id'"
        ).fetchone()
        if row is None:
            raise ValueError("Database has no map identity")
        stored = MapId.from_dict(json.loads(row["value"]))
        if stored != self.map_id:
            raise ValueError("Database map identity does not match requested MapId")

    def _ensure_additive_screen_columns(self) -> None:
        """Apply additive page-card columns to maps created by Aegis 1.0."""

        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(screens)").fetchall()
        }
        migrations = {
            "description": "ALTER TABLE screens ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "coverage_status": (
                "ALTER TABLE screens ADD COLUMN coverage_status "
                "TEXT NOT NULL DEFAULT 'DISCOVERED'"
            ),
        }
        with self.connection:
            for name, statement in migrations.items():
                if name not in columns:
                    self.connection.execute(statement)

    def close(self) -> None:
        """Close database and release the writer lock."""

        self.connection.close()
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None

    def __enter__(self) -> MapStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _touch(self) -> str:
        if self.readonly:
            raise sqlite3.OperationalError("Map store is read-only")
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE map_metadata SET value = ? WHERE key = 'updated_at'",
                (now,),
            )
        self.manifest = MapManifest(
            map_id=self.map_id,
            created_at=self.manifest.created_at,
            updated_at=now,
            generator=self.manifest.generator,
            database_file=self.manifest.database_file,
            artifact_type=self.manifest.artifact_type,
            schema_version=self.manifest.schema_version,
        )
        self.workspace.write_json_atomic(self.manifest_path, self.manifest.to_dict())
        return now

    def record_screen(
        self,
        observation: ScreenObservation,
        *,
        semantic_name: str | None = None,
        screen_id: str | None = None,
        business_domain: str | None = None,
    ) -> ScreenStateRecord:
        """Insert or refresh a screen state without overwriting human semantics."""

        self._require_writer()
        resolved_screen_id = screen_id or self._derive_screen_id(observation)
        state_id = self._state_id(observation)
        now = _utc_now()
        if semantic_name is not None and not semantic_name.strip():
            raise ValueError("semantic_name must be non-empty when provided")
        existing_screen = self.connection.execute(
            "SELECT semantic_name FROM screens WHERE screen_id = ?",
            (resolved_screen_id,),
        ).fetchone()
        existing_name = (
            str(existing_screen["semantic_name"]) if existing_screen is not None else None
        )
        initial_name = (
            semantic_name
            or existing_name
            or (observation.landmarks[0] if observation.landmarks else None)
            or f"screen-{state_id[:12]}"
        )
        description = (
            f"{initial_name}页面；关键标识："
            + ("、".join(observation.landmarks[:3]) if observation.landmarks else "暂无")
        )
        state_name = f"{observation.state_type.value}:{initial_name}"
        signature_json = _json(
            {
                "state_id": observation.signature.state_id,
                "structural_hash": observation.signature.structural_hash,
                "interactive_hash": observation.signature.interactive_hash,
                "landmark_hash": observation.signature.landmark_hash,
                "platform": observation.signature.platform.value,
                "node_count": observation.signature.node_count,
                "interactive_count": observation.signature.interactive_count,
                "landmark_count": observation.signature.landmark_count,
            }
        )
        structural_features = tuple(
            sorted(self._structural_features(observation))
        )
        interactive_features = tuple(
            sorted(self._interactive_features(observation))
        )

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO screens(
                    screen_id, semantic_name, screen_type, business_domain,
                    description, coverage_status, aliases_json, confidence,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'OBSERVED', '[]', ?, ?, ?)
                ON CONFLICT(screen_id) DO UPDATE SET
                    semantic_name = COALESCE(excluded.semantic_name, screens.semantic_name),
                    business_domain = COALESCE(excluded.business_domain, screens.business_domain),
                    description = CASE
                        WHEN screens.description = ''
                        THEN excluded.description ELSE screens.description END,
                    coverage_status = CASE
                        WHEN screens.coverage_status = 'DISCOVERED'
                        THEN 'OBSERVED' ELSE screens.coverage_status END,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_screen_id,
                    initial_name,
                    observation.state_type.value,
                    business_domain,
                    description,
                    0.6,
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO screen_states(
                    state_id, screen_id, state_name, state_type, package_name,
                    structural_hash, interactive_hash, landmark_hash, semantic_hash,
                    landmarks_json, structural_features_json, interactive_features_json,
                    signature_json, confidence, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state_id) DO UPDATE SET
                    screen_id = excluded.screen_id,
                    state_name = excluded.state_name,
                    state_type = excluded.state_type,
                    package_name = excluded.package_name,
                    structural_hash = excluded.structural_hash,
                    interactive_hash = excluded.interactive_hash,
                    landmark_hash = excluded.landmark_hash,
                    semantic_hash = excluded.semantic_hash,
                    landmarks_json = excluded.landmarks_json,
                    structural_features_json = excluded.structural_features_json,
                    interactive_features_json = excluded.interactive_features_json,
                    signature_json = excluded.signature_json,
                    confidence = MAX(screen_states.confidence, excluded.confidence),
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    state_id,
                    resolved_screen_id,
                    state_name,
                    observation.state_type.value,
                    observation.package_name,
                    observation.signature.structural_hash,
                    observation.signature.interactive_hash,
                    observation.signature.landmark_hash,
                    observation.semantic_hash,
                    _json(observation.landmarks),
                    _json(structural_features),
                    _json(interactive_features),
                    signature_json,
                    0.8,
                    now,
                    now,
                ),
            )
            searchable = " ".join(
                [
                    initial_name,
                    business_domain or "",
                    *observation.landmarks,
                ]
            )
            self.connection.execute(
                "DELETE FROM screen_search WHERE screen_id = ?",
                (resolved_screen_id,)
            )
            self.connection.execute(
                "INSERT INTO screen_search(screen_id, searchable_text) VALUES (?, ?)",
                (resolved_screen_id, searchable),
            )

        self._touch()
        stored = self.get_screen_state(state_id)
        if stored is None:
            raise RuntimeError("Screen state disappeared after upsert")
        return stored

    @staticmethod
    def _default_actions(element: UiElement) -> tuple[ExplorationAction, ...]:
        actions: list[ExplorationAction] = []
        is_back_navigation = element.content_desc.strip() in {
            "转到上一层级",
            "返回",
            "Back",
        }
        if element.clickable or element.checkable or element.actionable_ancestor:
            actions.extend([ExplorationAction.TAP, ExplorationAction.LONG_PRESS])
            if is_back_navigation:
                actions.remove(ExplorationAction.LONG_PRESS)
        if element.scrollable:
            actions.append(ExplorationAction.SCROLL)
        if element.hint or element.class_name.endswith(
            ("EditText", "TextField", "TextInputLayout")
        ):
            actions.append(ExplorationAction.INPUT_TEXT)
        return tuple(actions)

    @staticmethod
    def default_locators(element: UiElement, platform: Platform) -> tuple[ElementLocator, ...]:
        """Build conservative locator fallbacks from a normalized UI element."""

        locators: list[ElementLocator] = []
        if element.resource_id.strip():
            locators.append(
                ElementLocator(
                    platform=platform,
                    strategy="id",
                    value=element.resource_id.strip(),
                    priority=10,
                )
            )
        if element.accessibility_id.strip():
            locators.append(
                ElementLocator(
                    platform=platform,
                    strategy="accessibility_id",
                    value=element.accessibility_id.strip(),
                    priority=20,
                )
            )
        if element.content_desc.strip():
            locators.append(
                ElementLocator(
                    platform=platform,
                    strategy="label",
                    value=element.content_desc.strip(),
                    priority=30,
                )
            )
        if element.text.strip():
            locators.append(
                ElementLocator(
                    platform=platform,
                    strategy="text",
                    value=element.text.strip(),
                    priority=40,
                )
            )
        locators.append(
            ElementLocator(
                platform=platform,
                strategy="point",
                value=(
                    f"{element.bounds.center_x},{element.bounds.center_y}"
                ),
                priority=900,
                scope="device_observation",
            )
        )
        return tuple(locators)

    def record_element(
        self,
        state: ScreenStateRecord,
        element: UiElement,
        *,
        semantic_name: str | None = None,
        canonical_element_id: str | None = None,
        aliases: Sequence[str] = (),
        role: str | None = None,
        is_dynamic: bool = False,
        risk_level: str = "unknown",
        preconditions: Sequence[str] = (),
        locators: Sequence[ElementLocator] | None = None,
    ) -> ElementRecord:
        """Insert or refresh an element while preserving learned semantics."""

        self._require_writer()
        now = _utc_now()
        element_key = element.element_key
        element_id = f"{state.state_id}:{element_key}"
        resolved_locators = tuple(
            locators
            if locators is not None
            else self.default_locators(element, state.signature.platform)
        )
        bounds = element.bounds.to_list()
        actions = self._default_actions(element)

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO elements(
                    element_id, element_key, screen_id, state_id,
                    canonical_element_id, semantic_name, aliases_json, role,
                    is_actionable, is_dynamic, risk_level, actions_json,
                    preconditions_json, text, resource_id, accessibility_id,
                    content_desc, class_name, bounds_json, occurrence_index,
                    selector, confidence, first_seen_at, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state_id, element_key) DO UPDATE SET
                    screen_id = excluded.screen_id,
                    canonical_element_id = COALESCE(
                        excluded.canonical_element_id, elements.canonical_element_id
                    ),
                    semantic_name = COALESCE(excluded.semantic_name, elements.semantic_name),
                    aliases_json = CASE
                        WHEN excluded.aliases_json != '[]'
                        THEN excluded.aliases_json ELSE elements.aliases_json END,
                    role = COALESCE(excluded.role, elements.role),
                    is_actionable = excluded.is_actionable,
                    is_dynamic = excluded.is_dynamic,
                    risk_level = excluded.risk_level,
                    actions_json = excluded.actions_json,
                    preconditions_json = excluded.preconditions_json,
                    text = excluded.text,
                    resource_id = excluded.resource_id,
                    accessibility_id = excluded.accessibility_id,
                    content_desc = excluded.content_desc,
                    class_name = excluded.class_name,
                    bounds_json = excluded.bounds_json,
                    occurrence_index = excluded.occurrence_index,
                    selector = excluded.selector,
                    confidence = MAX(elements.confidence, excluded.confidence),
                    last_verified_at = excluded.last_verified_at
                """,
                (
                    element_id,
                    element_key,
                    state.screen_id,
                    state.state_id,
                    canonical_element_id,
                    semantic_name,
                    _json(aliases),
                    role or ("BUTTON" if element.clickable else "GROUP"),
                    int(
                        element.clickable
                        or element.scrollable
                        or element.checkable
                        or element.actionable_ancestor
                    ),
                    int(is_dynamic),
                    risk_level,
                    _json([action.value for action in actions]),
                    _json(preconditions),
                    element.text,
                    element.resource_id,
                    element.accessibility_id,
                    element.content_desc,
                    element.class_name,
                    _json(bounds),
                    element.occurrence_index,
                    element.selector,
                    0.7,
                    now,
                    now,
                ),
            )
            for locator in resolved_locators:
                if locator.platform != state.signature.platform:
                    raise ValueError(
                        f"Locator platform {locator.platform.value} does not match map platform"
                    )
                locator_id = _digest(
                    (
                        element_id,
                        locator.platform.value,
                        locator.strategy,
                        locator.value,
                        locator.scope,
                    )
                )
                self.connection.execute(
                    """
                    INSERT INTO locators(
                        locator_id, element_id, platform, strategy, value,
                        priority, scope, success_count, attempt_count,
                        last_error, last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(element_id, platform, strategy, value, scope) DO UPDATE SET
                        priority = MIN(locators.priority, excluded.priority),
                        last_error = NULL,
                        last_verified_at = excluded.last_verified_at
                    """,
                    (
                        locator_id,
                        element_id,
                        locator.platform.value,
                        locator.strategy,
                        locator.value,
                        locator.priority,
                        locator.scope,
                        locator.success_count,
                        locator.attempt_count,
                        locator.last_error,
                        locator.last_verified_at,
                    ),
                )

            screen_row = self.connection.execute(
                "SELECT semantic_name FROM screens WHERE screen_id = ?",
                (state.screen_id,),
            ).fetchone()
            screen_name = str(screen_row["semantic_name"]) if screen_row else ""
            searchable = " ".join(
                filter(
                    None,
                    [
                        semantic_name,
                        *aliases,
                        element.text,
                        element.content_desc,
                        element.accessibility_id,
                        element.resource_id,
                        element.class_name,
                        element.selector,
                        screen_name,
                    ],
                )
            )
            self.connection.execute(
                "DELETE FROM element_search WHERE element_id = ?",
                (element_id,),
            )
            self.connection.execute(
                "INSERT INTO element_search(element_id, searchable_text) VALUES (?, ?)",
                (element_id, searchable),
            )

        self._touch()
        stored = self.get_element(element_id)
        if stored is None:
            raise RuntimeError("Element disappeared after upsert")
        return stored

    def record_observation(
        self,
        observation: ScreenObservation,
        *,
        run_id: str,
        state_id: str,
        device_id: str = "",
        screenshot_path: str | None = None,
        account_state: str = "",
        network_state: str = "",
        trigger_transition_id: str | None = None,
        observation_id: str | None = None,
    ) -> ObservationRecord:
        """Record immutable observation metadata without raw hierarchy blobs."""

        self._require_writer()
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        if screenshot_path is not None and Path(screenshot_path).is_absolute():
            raise ValueError("screenshot_path must be relative to the map artifact")
        if screenshot_path is not None:
            screenshot = Path(screenshot_path)
            if (
                ".." in screenshot.parts
                or not screenshot.parts
                or screenshot.parts[0] != "screenshots"
            ):
                raise ValueError("screenshot_path must stay inside the map screenshots directory")
        state = self.get_screen_state(state_id)
        if state is None:
            raise ValueError(f"Unknown screen state: {state_id}")
        resolved_id = observation_id or f"obs-{uuid.uuid4().hex}"
        now = _utc_now()
        signature_json = _json(
            {
                "state_id": observation.signature.state_id,
                "structural_hash": observation.signature.structural_hash,
                "interactive_hash": observation.signature.interactive_hash,
                "landmark_hash": observation.signature.landmark_hash,
                "platform": observation.signature.platform.value,
                "node_count": observation.signature.node_count,
                "interactive_count": observation.signature.interactive_count,
                "landmark_count": observation.signature.landmark_count,
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO observations(
                    observation_id, run_id, state_id, observed_at,
                    structural_hash, interactive_hash, landmark_hash, state_type,
                    package_name, device_id, screenshot_path, account_state,
                    network_state, trigger_transition_id, signature_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    state_id = excluded.state_id,
                    observed_at = excluded.observed_at,
                    structural_hash = excluded.structural_hash,
                    interactive_hash = excluded.interactive_hash,
                    landmark_hash = excluded.landmark_hash,
                    state_type = excluded.state_type,
                    package_name = excluded.package_name,
                    device_id = excluded.device_id,
                    screenshot_path = excluded.screenshot_path,
                    account_state = excluded.account_state,
                    network_state = excluded.network_state,
                    trigger_transition_id = excluded.trigger_transition_id,
                    signature_json = excluded.signature_json
                """,
                (
                    resolved_id,
                    run_id,
                    state_id,
                    now,
                    observation.signature.structural_hash,
                    observation.signature.interactive_hash,
                    observation.signature.landmark_hash,
                    observation.state_type.value,
                    observation.package_name,
                    device_id,
                    screenshot_path,
                    account_state,
                    network_state,
                    trigger_transition_id,
                    signature_json,
                ),
            )
        self._touch()
        return ObservationRecord(
            observation_id=resolved_id,
            run_id=run_id,
            state_id=state_id,
            observed_at=now,
            structural_hash=observation.signature.structural_hash,
            interactive_hash=observation.signature.interactive_hash,
            landmark_hash=observation.signature.landmark_hash,
            state_type=observation.state_type,
            package_name=observation.package_name,
            device_id=device_id,
            screenshot_path=screenshot_path,
            account_state=account_state,
            network_state=network_state,
            trigger_transition_id=trigger_transition_id,
            signature=observation.signature,
        )

    def record_transition(
        self,
        *,
        from_state_id: str,
        action: ExplorationAction,
        result_type: ActionResultType,
        to_state_id: str | None = None,
        element_id: str | None = None,
        maestro_commands: Sequence[Mapping[str, Any]] = (),
        restore_strategy: Sequence[Mapping[str, Any]] = (),
        success: bool = True,
        transition_id: str | None = None,
    ) -> TransitionRecord:
        """Record or increment an observed transition."""

        self._require_writer()
        if from_state_id == to_state_id:
            raise ValueError("A transition must not point to itself")
        target_required = result_type in {
            ActionResultType.NEW_PAGE,
            ActionResultType.OVERLAY,
            ActionResultType.STATE_CHANGE,
        }
        target_forbidden = result_type in {
            ActionResultType.NO_OP,
            ActionResultType.EXTERNAL_APP,
            ActionResultType.APP_EXIT,
            ActionResultType.CRASH,
            ActionResultType.BLOCKED,
            ActionResultType.ERROR,
        }
        if target_required and to_state_id is None:
            raise ValueError(f"result_type {result_type.value} requires to_state_id")
        if target_forbidden and to_state_id is not None:
            raise ValueError(f"result_type {result_type.value} must not have to_state_id")
        if to_state_id is not None and self.get_screen_state(to_state_id) is None:
            raise ValueError(f"Unknown target screen state: {to_state_id}")
        if self.get_screen_state(from_state_id) is None:
            raise ValueError(f"Unknown source screen state: {from_state_id}")
        if element_id is not None and self.get_element(element_id) is None:
            raise ValueError(f"Unknown element: {element_id}")

        resolved_id = transition_id or _digest(
            (from_state_id, element_id, action.value, result_type.value, to_state_id)
        )
        now = _utc_now()
        with self.connection:
            existing = self.connection.execute(
                """
                SELECT transition_id FROM transitions
                WHERE from_state_id = ?
                  AND element_id IS ?
                  AND action = ?
                  AND result_type = ?
                  AND to_state_id IS ?
                """,
                (
                    from_state_id,
                    element_id,
                    action.value,
                    result_type.value,
                    to_state_id,
                ),
            ).fetchone()
            if existing is not None:
                existing_id = str(existing["transition_id"])
                if transition_id is not None and transition_id != existing_id:
                    raise ValueError(
                        "A different transition_id already exists for this identity"
                    )
                self.connection.execute(
                    """
                    UPDATE transitions SET
                        maestro_commands_json = ?,
                        restore_strategy_json = ?,
                        observed_count = observed_count + 1,
                        success_count = success_count + ?,
                        last_verified_at = ?
                    WHERE transition_id = ?
                    """,
                    (
                        _json(list(maestro_commands)),
                        _json(list(restore_strategy)),
                        int(success),
                        now,
                        existing_id,
                    ),
                )
                resolved_id = existing_id
            else:
                self.connection.execute(
                    """
                    INSERT INTO transitions(
                        transition_id, from_state_id, element_id, action, result_type,
                        to_state_id, maestro_commands_json, restore_strategy_json,
                        observed_count, success_count, last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        resolved_id,
                        from_state_id,
                        element_id,
                        action.value,
                        result_type.value,
                        to_state_id,
                        _json(list(maestro_commands)),
                        _json(list(restore_strategy)),
                        int(success),
                        now,
                    ),
                )
        self._touch()
        stored = self.get_transition(resolved_id)
        if stored is None:
            raise RuntimeError("Transition disappeared after upsert")
        if stored.to_state_id is not None and stored.success_count > 0:
            self._record_path_edge(stored)
        return stored

    def _record_path_edge(self, transition: TransitionRecord) -> str:
        """Cache one verified edge so page routes survive process interruptions."""

        assert transition.to_state_id is not None
        path_id = _digest(
            (
                "screen_state",
                transition.to_state_id,
                "screen_state",
                transition.from_state_id,
            )
        )
        steps = [
            {
                "transition_id": transition.transition_id,
                "from_state_id": transition.from_state_id,
                "to_state_id": transition.to_state_id,
                "element_id": transition.element_id,
                "action": transition.action.value,
                "maestro_commands": list(transition.maestro_commands),
            }
        ]
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO paths(
                    path_id, target_type, target_id, from_type, from_id,
                    steps_json, success_count, attempt_count, last_verified_at
                ) VALUES (?, 'screen_state', ?, 'screen_state', ?, ?, 1, 1, ?)
                ON CONFLICT(target_type, target_id, from_type, from_id) DO UPDATE SET
                    steps_json = excluded.steps_json,
                    success_count = paths.success_count + 1,
                    attempt_count = paths.attempt_count + 1,
                    last_verified_at = excluded.last_verified_at
                """,
                (
                    path_id,
                    transition.to_state_id,
                    transition.from_state_id,
                    _json(steps),
                    now,
                ),
            )
        return path_id

    def materialize_paths(self) -> int:
        """Backfill reliable entry-to-page paths from verified transitions."""

        self._require_writer()
        entry = self.get_entry_state()
        if entry is None:
            return 0
        adjacency: dict[str, list[TransitionRecord]] = {}
        for transition in self.list_transitions():
            if transition.to_state_id is None or transition.success_count <= 0:
                continue
            if not transition.maestro_commands:
                continue
            adjacency.setdefault(transition.from_state_id, []).append(transition)

        created = 0
        queue: list[tuple[str, tuple[TransitionRecord, ...]]] = [
            (entry.state_id, ())
        ]
        visited: set[str] = set()
        while queue:
            state_id, route = queue.pop(0)
            if state_id in visited:
                continue
            visited.add(state_id)
            if route:
                created += self._upsert_entry_path(entry.state_id, state_id, route)
            for transition in adjacency.get(state_id, ()):
                if transition.to_state_id is None or transition.to_state_id in visited:
                    continue
                queue.append((transition.to_state_id, (*route, transition)))
        if created:
            self._touch()
        return created

    def _upsert_entry_path(
        self,
        from_state_id: str,
        to_state_id: str,
        transitions: Sequence[TransitionRecord],
    ) -> int:
        """Insert a complete multi-step route without inflating replay counters."""

        path_id = _digest(
            ("screen_state", to_state_id, "screen_state", from_state_id)
        )
        steps = [
            {
                "transition_id": transition.transition_id,
                "from_state_id": transition.from_state_id,
                "to_state_id": transition.to_state_id,
                "element_id": transition.element_id,
                "action": transition.action.value,
                "maestro_commands": list(transition.maestro_commands),
            }
            for transition in transitions
            if transition.to_state_id is not None
        ]
        existing = self.connection.execute(
            """
            SELECT path_id FROM paths
            WHERE target_type = 'screen_state'
              AND target_id = ?
              AND from_type = 'screen_state'
              AND from_id = ?
            """,
            (to_state_id, from_state_id),
        ).fetchone()
        with self.connection:
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO paths(
                        path_id, target_type, target_id, from_type, from_id,
                        steps_json, success_count, attempt_count, last_verified_at
                    ) VALUES (?, 'screen_state', ?, 'screen_state', ?, ?, 1, 1, ?)
                    """,
                    (
                        path_id,
                        to_state_id,
                        from_state_id,
                        _json(steps),
                        _utc_now(),
                    ),
                )
                return 1
            self.connection.execute(
                """
                UPDATE paths SET
                    steps_json = ?, last_verified_at = ?
                WHERE target_type = 'screen_state'
                  AND target_id = ?
                  AND from_type = 'screen_state'
                  AND from_id = ?
                """,
                (
                    _json(steps),
                    _utc_now(),
                    to_state_id,
                    from_state_id,
                ),
            )
        return 0

    def get_screen_state(self, state_id: str) -> ScreenStateRecord | None:
        row = self.connection.execute(
            "SELECT * FROM screen_states WHERE state_id = ?",
            (state_id,),
        ).fetchone()
        return self._row_to_state(row) if row is not None else None

    def list_screen_states(self, *, screen_id: str | None = None) -> list[ScreenStateRecord]:
        if screen_id is None:
            rows = self.connection.execute(
                "SELECT * FROM screen_states ORDER BY first_seen_at, state_id"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM screen_states WHERE screen_id = ? ORDER BY first_seen_at, state_id",
                (screen_id,),
            ).fetchall()
        return [self._row_to_state(row) for row in rows]

    def get_screen(self, screen_id: str) -> ScreenRecord | None:
        row = self.connection.execute(
            "SELECT * FROM screens WHERE screen_id = ?",
            (screen_id,),
        ).fetchone()
        if row is None:
            return None
        return ScreenRecord(
            screen_id=str(row["screen_id"]),
            semantic_name=str(row["semantic_name"]),
            screen_type=str(row["screen_type"]),
            business_domain=row["business_domain"],
            description=str(row["description"]) if "description" in row.keys() else "",
            coverage_status=(
                str(row["coverage_status"])
                if "coverage_status" in row.keys()
                else "DISCOVERED"
            ),
            aliases=tuple(json.loads(row["aliases_json"])),
            confidence=float(row["confidence"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_page_summaries(self) -> list[PageSummary]:
        """Return compact Chinese-friendly page cards for AI consumption."""

        has_description = self._has_screen_column("description")
        has_coverage = self._has_screen_column("coverage_status")
        description_expression = (
            "s.description" if has_description else "''"
        )
        coverage_expression = (
            "s.coverage_status" if has_coverage else "'DISCOVERED'"
        )
        rows = self.connection.execute(
            f"""
            SELECT
                s.screen_id,
                s.semantic_name,
                s.screen_type,
                s.business_domain,
                {description_expression} AS description,
                {coverage_expression} AS coverage_status,
                s.aliases_json,
                s.updated_at,
                (SELECT COUNT(*) FROM screen_states ss WHERE ss.screen_id = s.screen_id) AS state_count,
                (SELECT COUNT(*) FROM elements e WHERE e.screen_id = s.screen_id) AS element_count,
                (
                    SELECT COUNT(*) FROM transitions t
                    JOIN screen_states fs ON fs.state_id = t.from_state_id
                    WHERE fs.screen_id = s.screen_id
                ) AS outgoing_transition_count,
                (
                    SELECT COUNT(*) FROM transitions t
                    JOIN screen_states ts ON ts.state_id = t.to_state_id
                    WHERE ts.screen_id = s.screen_id
                ) AS incoming_transition_count
            FROM screens s
            ORDER BY s.updated_at DESC, s.semantic_name, s.screen_id
            """
        ).fetchall()
        return [
            PageSummary(
                screen_id=str(row["screen_id"]),
                name=str(row["semantic_name"]),
                description=str(row["description"]),
                screen_type=str(row["screen_type"]),
                business_domain=row["business_domain"],
                coverage_status=str(row["coverage_status"]),
                aliases=tuple(str(item) for item in json.loads(row["aliases_json"])),
                state_count=int(row["state_count"]),
                element_count=int(row["element_count"]),
                incoming_transition_count=int(row["incoming_transition_count"]),
                outgoing_transition_count=int(row["outgoing_transition_count"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def update_screen_card(
        self,
        screen_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        aliases: Sequence[str] | None = None,
        coverage_status: str | None = None,
    ) -> ScreenRecord:
        """Update human-readable page metadata without touching raw observations."""

        self._require_writer()
        allowed_statuses = {
            "DISCOVERED",
            "OBSERVED",
            "BASELINE_DONE",
            "DEEP_PENDING",
            "DEEP_DONE",
            "BLOCKED",
            "NEEDS_HUMAN",
            "OUT_OF_SCOPE",
            "EXTERNAL",
        }
        if name is not None and not name.strip():
            raise ValueError("page name must be non-empty")
        if description is not None and not description.strip():
            raise ValueError("page description must be non-empty")
        if coverage_status is not None and coverage_status not in allowed_statuses:
            raise ValueError(f"Unsupported page coverage status: {coverage_status}")
        current = self.get_screen(screen_id)
        if current is None:
            raise ValueError(f"Unknown screen: {screen_id}")
        resolved_aliases = current.aliases if aliases is None else tuple(
            dict.fromkeys(alias.strip() for alias in aliases if alias.strip())
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE screens SET
                    semantic_name = ?,
                    description = ?,
                    coverage_status = ?,
                    aliases_json = ?,
                    updated_at = ?
                WHERE screen_id = ?
                """,
                (
                    current.semantic_name if name is None else name.strip(),
                    current.description if description is None else description.strip(),
                    current.coverage_status
                    if coverage_status is None
                    else coverage_status,
                    _json(list(resolved_aliases)),
                    _utc_now(),
                    screen_id,
                ),
            )
        self._touch()
        updated = self.get_screen(screen_id)
        if updated is None:
            raise RuntimeError("Screen disappeared after update")
        return updated

    def mark_screen_coverage(self, screen_id: str, coverage_status: str) -> ScreenRecord:
        """Update automatic coverage while preserving explicit human statuses."""

        self._require_writer()
        current = self.get_screen(screen_id)
        if current is None:
            raise ValueError(f"Unknown screen: {screen_id}")
        human_statuses = {"BLOCKED", "NEEDS_HUMAN", "OUT_OF_SCOPE", "EXTERNAL"}
        if current.coverage_status in human_statuses:
            return current
        if coverage_status not in {
            "DISCOVERED",
            "OBSERVED",
            "BASELINE_DONE",
            "DEEP_PENDING",
            "DEEP_DONE",
            "BLOCKED",
            "NEEDS_HUMAN",
            "OUT_OF_SCOPE",
            "EXTERNAL",
        }:
            raise ValueError(f"Unsupported page coverage status: {coverage_status}")
        if current.coverage_status == coverage_status:
            return current
        with self.connection:
            self.connection.execute(
                """
                UPDATE screens SET coverage_status = ?, updated_at = ?
                WHERE screen_id = ?
                """,
                (coverage_status, _utc_now(), screen_id),
            )
        self._touch()
        updated = self.get_screen(screen_id)
        if updated is None:
            raise RuntimeError("Screen disappeared after coverage update")
        return updated

    def _has_screen_column(self, column: str) -> bool:
        return any(
            str(row["name"]) == column
            for row in self.connection.execute("PRAGMA table_info(screens)")
        )

    def set_entry_state(self, state_id: str) -> None:
        """Persist the canonical entry state for path planning."""

        self._require_writer()
        if self.get_screen_state(state_id) is None:
            raise ValueError(f"Unknown screen state: {state_id}")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO map_metadata(key, value) VALUES ('entry_state_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (state_id,),
            )
        self._touch()

    def get_entry_state(self) -> ScreenStateRecord | None:
        """Return the explicit entry state or a conservative fallback."""

        row = self.connection.execute(
            "SELECT value FROM map_metadata WHERE key = 'entry_state_id'"
        ).fetchone()
        if row is not None:
            state = self.get_screen_state(str(row["value"]))
            if state is not None:
                return state

        observed = self.connection.execute(
            """
            SELECT state_id FROM observations
            ORDER BY observed_at, observation_id
            LIMIT 1
            """
        ).fetchone()
        if observed is not None:
            state = self.get_screen_state(str(observed["state_id"]))
            if state is not None:
                return state

        root = self.connection.execute(
            """
            SELECT screen_states.state_id
            FROM screen_states
            LEFT JOIN transitions
              ON transitions.to_state_id = screen_states.state_id
            WHERE transitions.transition_id IS NULL
            ORDER BY screen_states.first_seen_at, screen_states.state_id
            LIMIT 1
            """
        ).fetchone()
        return self.get_screen_state(str(root["state_id"])) if root is not None else None

    def get_element(self, element_id: str) -> ElementRecord | None:
        row = self.connection.execute(
            "SELECT * FROM elements WHERE element_id = ?",
            (element_id,),
        ).fetchone()
        return self._row_to_element(row) if row is not None else None

    def list_elements(self, *, state_id: str | None = None) -> list[ElementRecord]:
        if state_id is None:
            rows = self.connection.execute(
                "SELECT * FROM elements ORDER BY state_id, selector, occurrence_index"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM elements WHERE state_id = ? ORDER BY selector, occurrence_index",
                (state_id,),
            ).fetchall()
        return [self._row_to_element(row) for row in rows]

    def get_transition(self, transition_id: str) -> TransitionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM transitions WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
        return self._row_to_transition(row) if row is not None else None

    def list_transitions(
        self,
        *,
        from_state_id: str | None = None,
        to_state_id: str | None = None,
    ) -> list[TransitionRecord]:
        """List transitions in deterministic graph order."""

        clauses: list[str] = []
        parameters: list[Any] = []
        if from_state_id is not None:
            clauses.append("from_state_id = ?")
            parameters.append(from_state_id)
        if to_state_id is not None:
            clauses.append("to_state_id = ?")
            parameters.append(to_state_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM transitions {where} ORDER BY from_state_id, to_state_id, transition_id",
            parameters,
        ).fetchall()
        return [self._row_to_transition(row) for row in rows]

    def record_transition_result(
        self,
        transition_id: str,
        *,
        success: bool,
    ) -> TransitionRecord | None:
        """Increment replay verification counters for a transition."""

        self._require_writer()
        record = self.get_transition(transition_id)
        if record is None:
            raise ValueError(f"Unknown transition: {transition_id}")
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE transitions SET
                    observed_count = observed_count + 1,
                    success_count = success_count + ?,
                    last_verified_at = ?
                WHERE transition_id = ?
                """,
                (int(success), now, transition_id),
            )
        self._touch()
        return self.get_transition(transition_id)

    def record_locator_result(
        self,
        locator_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> ElementRecord | None:
        """Record a locator replay result and touch its element verification time."""

        self._require_writer()
        row = self.connection.execute(
            "SELECT element_id FROM locators WHERE locator_id = ?",
            (locator_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown locator: {locator_id}")
        element_id = str(row["element_id"])
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE locators SET
                    attempt_count = attempt_count + 1,
                    success_count = success_count + ?,
                    last_error = ?,
                    last_verified_at = ?
                WHERE locator_id = ?
                """,
                (int(success), None if success else error, now, locator_id),
            )
            self.connection.execute(
                "UPDATE elements SET last_verified_at = ? WHERE element_id = ?",
                (now, element_id),
            )
        self._touch()
        return self.get_element(element_id)

    def search_elements(self, query: str, *, limit: int = 10) -> list[ElementSearchResult]:
        if not query.strip():
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        escaped = '"' + query.replace('"', '""') + '"'
        rows = self.connection.execute(
            """
            SELECT element_id, rank, 'fts' AS source
            FROM element_search
            WHERE element_search MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (escaped, limit),
        ).fetchall()
        results = [
            ElementSearchResult(
                element=self.get_element(str(row["element_id"])),
                score=1.0 / (1.0 + abs(float(row["rank"]))),
                matched_source="fts",
            )
            for row in rows
            if self.get_element(str(row["element_id"])) is not None
        ]
        if results:
            return results[:limit]

        pattern = f"%{query.lower()}%"
        rows = self.connection.execute(
            """
            SELECT element_id, searchable_text
            FROM element_search
            WHERE lower(searchable_text) LIKE ?
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
        return [
            ElementSearchResult(
                element=element,
                score=0.5,
                matched_source="substring",
            )
            for row in rows
            if (element := self.get_element(str(row["element_id"]))) is not None
        ]

    def statistics(self) -> dict[str, int]:
        return {
            "screens": self._count("screens"),
            "screen_states": self._count("screen_states"),
            "elements": self._count("elements"),
            "locators": self._count("locators"),
            "transitions": self._count("transitions"),
            "observations": self._count("observations"),
            "paths": self._count("paths"),
            "embeddings": self._count("embeddings"),
        }

    def record_element_embedding(
        self,
        element_id: str,
        vector: Sequence[float],
        *,
        model_name: str,
        model_version: str,
        searchable_text: str,
    ) -> str:
        """Store one semantic vector owned by this map artifact."""

        self._require_writer()
        element = self.get_element(element_id)
        if element is None:
            raise ValueError(f"Unknown element: {element_id}")
        if not model_name.strip() or not model_version.strip():
            raise ValueError("Embedding model name and version must be non-empty")
        if not vector:
            raise ValueError("Embedding vector cannot be empty")
        if len(vector) > 4096:
            raise ValueError("Embedding dimension cannot exceed 4096")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding vector contains non-finite values")

        embedding_id = _digest(
            ("element", element_id, model_name, model_version)
        )
        packed = struct.pack(f"<{len(vector)}f", *vector)
        text_hash = hashlib.sha256(searchable_text.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO embeddings(
                    embedding_id, target_type, target_id, model_name, model_version,
                    text_hash, dimension, vector_blob, created_at, updated_at
                ) VALUES (?, 'element', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_type, target_id, model_name, model_version) DO UPDATE SET
                    text_hash = excluded.text_hash,
                    dimension = excluded.dimension,
                    vector_blob = excluded.vector_blob,
                    updated_at = excluded.updated_at
                """,
                (
                    embedding_id,
                    element_id,
                    model_name,
                    model_version,
                    text_hash,
                    len(vector),
                    packed,
                    now,
                    now,
                ),
            )
        self._touch()
        return embedding_id

    def search_elements_by_vector(
        self,
        vector: Sequence[float],
        *,
        model_name: str,
        model_version: str,
        limit: int = 10,
    ) -> list[ElementSearchResult]:
        """Cosine-search element embeddings contained in this map."""

        if not vector or not all(math.isfinite(value) for value in vector):
            raise ValueError("Query vector must be non-empty and finite")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        rows = self.connection.execute(
            """
            SELECT target_id, dimension, vector_blob
            FROM embeddings
            WHERE target_type = 'element'
              AND model_name = ?
              AND model_version = ?
            """,
            (model_name, model_version),
        ).fetchall()
        query_norm = math.sqrt(sum(value * value for value in vector))
        if query_norm == 0:
            return []

        scored: list[tuple[float, str]] = []
        for row in rows:
            dimension = int(row["dimension"])
            if dimension != len(vector):
                continue
            candidate = struct.unpack(f"<{dimension}f", bytes(row["vector_blob"]))
            candidate_norm = math.sqrt(sum(value * value for value in candidate))
            if candidate_norm == 0:
                continue
            score = sum(a * b for a, b in zip(vector, candidate)) / (
                query_norm * candidate_norm
            )
            scored.append((score, str(row["target_id"])))

        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[ElementSearchResult] = []
        for score, element_id in scored[:limit]:
            element = self.get_element(element_id)
            if element is not None:
                results.append(
                    ElementSearchResult(
                        element=element,
                        score=score,
                        matched_source=f"embedding:{model_name}:{model_version}",
                    )
                )
        return results

    def search_screens(self, query: str, *, limit: int = 10) -> list[ScreenSearchResult]:
        """Search logical screens using the map-local FTS index."""

        if not query.strip():
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        escaped = '"' + query.replace('"', '""') + '"'
        rows = self.connection.execute(
            """
            SELECT screen_id, rank FROM screen_search
            WHERE screen_search MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (escaped, limit),
        ).fetchall()
        source = "fts"
        if not rows:
            rows = self.connection.execute(
                """
                SELECT screen_id, 0 AS rank FROM screen_search
                WHERE lower(searchable_text) LIKE ?
                LIMIT ?
                """,
                (f"%{query.lower()}%", limit),
            ).fetchall()
            source = "substring"

        results: list[ScreenSearchResult] = []
        for row in rows:
            screen_id = str(row["screen_id"])
            screen = self.connection.execute(
                "SELECT semantic_name FROM screens WHERE screen_id = ?",
                (screen_id,),
            ).fetchone()
            if screen is None:
                continue
            states = self.connection.execute(
                "SELECT state_id FROM screen_states WHERE screen_id = ? ORDER BY state_id",
                (screen_id,),
            ).fetchall()
            score = 1.0 / (1.0 + abs(float(row["rank"]))) if source == "fts" else 0.5
            results.append(
                ScreenSearchResult(
                    screen_id=screen_id,
                    semantic_name=str(screen["semantic_name"]),
                    score=score,
                    matched_source=source,
                    state_ids=tuple(str(state["state_id"]) for state in states),
                )
            )
        return results

    def export_json(self) -> Path:
        """Export a deterministic JSON snapshot inside the map artifact."""

        output = self.map_directory / "exports" / "map.json"
        data = {
            "manifest": self.manifest.to_dict(),
            "statistics": self.statistics(),
            "screens": [dict(row) for row in self.connection.execute(
                "SELECT * FROM screens ORDER BY screen_id"
            )],
            "screen_states": [dict(row) for row in self.connection.execute(
                "SELECT * FROM screen_states ORDER BY state_id"
            )],
            "elements": [dict(row) for row in self.connection.execute(
                "SELECT * FROM elements ORDER BY element_id"
            )],
            "locators": [dict(row) for row in self.connection.execute(
                "SELECT * FROM locators ORDER BY locator_id"
            )],
            "transitions": [dict(row) for row in self.connection.execute(
                "SELECT * FROM transitions ORDER BY transition_id"
            )],
            "observations": [dict(row) for row in self.connection.execute(
                "SELECT * FROM observations ORDER BY observed_at, observation_id"
            )],
            "paths": [dict(row) for row in self.connection.execute(
                "SELECT * FROM paths ORDER BY path_id"
            )],
            "embeddings": [
                {
                    **dict(row),
                    "vector_blob": base64.b64encode(bytes(row["vector_blob"])).decode("ascii"),
                }
                for row in self.connection.execute(
                    "SELECT * FROM embeddings ORDER BY embedding_id"
                )
            ],
        }
        self.workspace.write_json_atomic(output, data)
        return output

    def _require_writer(self) -> None:
        if self.readonly:
            raise sqlite3.OperationalError("Map store is read-only")

    def _count(self, table: str) -> int:
        if table not in {
            "screens", "screen_states", "elements", "locators",
            "transitions", "observations", "paths", "embeddings",
        }:
            raise ValueError("Invalid table name")
        return int(self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])

    @staticmethod
    def _derive_screen_id(observation: ScreenObservation) -> str:
        from aegis_cartographer.core.identity import derive_screen_id

        return derive_screen_id(observation)

    @staticmethod
    def _state_id(observation: ScreenObservation) -> str:
        from aegis_cartographer.core.identity import derive_state_id

        return derive_state_id(observation)

    @staticmethod
    def _structural_features(observation: ScreenObservation) -> dict[str, int]:
        from aegis_cartographer.core.identity import _feature_set

        return dict(_feature_set(observation.hierarchy, interactive_only=False))

    @staticmethod
    def _interactive_features(observation: ScreenObservation) -> dict[str, int]:
        from aegis_cartographer.core.identity import _feature_set

        return dict(_feature_set(observation.hierarchy, interactive_only=True))

    def _row_to_state(self, row: Mapping[str, Any]) -> ScreenStateRecord:
        signature_data = json.loads(row["signature_json"])
        signature = ScreenSignature(
            state_id=str(signature_data["state_id"]),
            structural_hash=str(signature_data["structural_hash"]),
            interactive_hash=str(signature_data["interactive_hash"]),
            landmark_hash=str(signature_data["landmark_hash"]),
            platform=Platform(str(signature_data["platform"])),
            node_count=int(signature_data["node_count"]),
            interactive_count=int(signature_data["interactive_count"]),
            landmark_count=int(signature_data["landmark_count"]),
        )
        return ScreenStateRecord(
            state_id=str(row["state_id"]),
            screen_id=str(row["screen_id"]),
            state_type=ScreenStateType(str(row["state_type"])),
            signature=signature,
            landmarks=tuple(json.loads(row["landmarks_json"])),
            semantic_hash=str(row["semantic_hash"]),
            package_name=str(row["package_name"]),
            structural_features=tuple(json.loads(row["structural_features_json"])),
            interactive_features=tuple(json.loads(row["interactive_features_json"])),
        )

    def _row_to_element(self, row: Mapping[str, Any]) -> ElementRecord:
        locator_rows = self.connection.execute(
            "SELECT * FROM locators WHERE element_id = ? ORDER BY priority, locator_id",
            (row["element_id"],),
        ).fetchall()
        locators = tuple(self._row_to_locator(locator) for locator in locator_rows)
        return ElementRecord(
            element_id=str(row["element_id"]),
            element_key=str(row["element_key"]),
            screen_id=str(row["screen_id"]),
            state_id=str(row["state_id"]),
            semantic_name=row["semantic_name"],
            aliases=tuple(json.loads(row["aliases_json"])),
            canonical_element_id=row["canonical_element_id"],
            role=str(row["role"]),
            is_actionable=_bool(row["is_actionable"]),
            is_dynamic=_bool(row["is_dynamic"]),
            risk_level=str(row["risk_level"]),
            actions=tuple(ExplorationAction(value) for value in json.loads(row["actions_json"])),
            preconditions=tuple(json.loads(row["preconditions_json"])),
            text=str(row["text"]),
            resource_id=str(row["resource_id"]),
            accessibility_id=str(row["accessibility_id"]),
            content_desc=str(row["content_desc"]),
            class_name=str(row["class_name"]),
            bounds=tuple(int(value) for value in json.loads(row["bounds_json"])),
            occurrence_index=int(row["occurrence_index"]),
            selector=str(row["selector"]),
            confidence=float(row["confidence"]),
            first_seen_at=str(row["first_seen_at"]),
            last_verified_at=row["last_verified_at"],
            locators=locators,
        )

    @staticmethod
    def _row_to_locator(row: Mapping[str, Any]) -> ElementLocator:
        return ElementLocator(
            platform=Platform(str(row["platform"])),
            strategy=str(row["strategy"]),
            value=str(row["value"]),
            priority=int(row["priority"]),
            scope=str(row["scope"]),
            success_count=int(row["success_count"]),
            attempt_count=int(row["attempt_count"]),
            last_error=row["last_error"],
            last_verified_at=row["last_verified_at"],
            locator_id=str(row["locator_id"]),
        )

    @staticmethod
    def _row_to_transition(row: Mapping[str, Any]) -> TransitionRecord:
        return TransitionRecord(
            transition_id=str(row["transition_id"]),
            from_state_id=str(row["from_state_id"]),
            element_id=row["element_id"],
            action=ExplorationAction(str(row["action"])),
            result_type=ActionResultType(str(row["result_type"])),
            to_state_id=row["to_state_id"],
            maestro_commands=tuple(json.loads(row["maestro_commands_json"])),
            restore_strategy=tuple(json.loads(row["restore_strategy_json"])),
            observed_count=int(row["observed_count"]),
            success_count=int(row["success_count"]),
            last_verified_at=row["last_verified_at"],
        )
