"""Project workspace layout for isolated Aegis artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from aegis_cartographer.core.storage.models import MapId


class ProjectWorkspace:
    """Locate project-local Aegis data without polluting the engine installation."""

    def __init__(self, project_root: str | Path) -> None:
        root = Path(project_root).expanduser()
        if not root.exists():
            raise FileNotFoundError(f"Project root does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {root}")
        self.root = root.resolve()
        self.aegis_dir = self.root / ".aegis"
        self.maps_dir = self.aegis_dir / "maps"
        self.runs_dir = self.aegis_dir / "runs"
        self.logs_dir = self.aegis_dir / "logs"
        self.config_path = self.aegis_dir / "config.json"

    def ensure_layout(self) -> None:
        """Create the persistent and transient workspace directories."""

        for directory in (self.aegis_dir, self.maps_dir, self.runs_dir, self.logs_dir):
            if directory.is_symlink():
                raise ValueError(f"Aegis workspace path must not be a symlink: {directory}")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                directory.resolve().relative_to(self.root.resolve())
            except ValueError as error:
                raise ValueError("Aegis workspace directory escapes the project root") from error

    def map_directory(self, map_id: MapId) -> Path:
        """Return the isolated directory for one map identity."""

        directory = self.maps_dir / map_id.relative_directory
        if directory.is_symlink():
            raise ValueError(f"Map directory must not be a symlink: {directory}")
        self._ensure_inside_maps(directory)
        return directory

    def resolve_resource(self, relative_path: str | Path) -> Path:
        """Resolve a resource path and prevent escapes from `.aegis`."""

        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError("Map resources must use project-relative paths")
        resolved = (self.aegis_dir / candidate).resolve()
        try:
            resolved.relative_to(self.aegis_dir.resolve())
        except ValueError as error:
            raise ValueError("Resource path escapes the Aegis workspace") from error
        return resolved

    def write_json_atomic(self, path: Path, value: Mapping[str, Any]) -> None:
        """Write JSON with fsync and atomic replacement."""

        target = path.resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as error:
            raise ValueError("Atomic writes must remain inside the project root") from error
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
            text=True,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def read_json(self, path: Path) -> dict[str, Any]:
        """Read a JSON object from a workspace file."""

        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError(f"Expected a JSON object in {path}")
        return value

    def _ensure_inside_maps(self, directory: Path) -> None:
        try:
            directory.resolve().relative_to(self.maps_dir.resolve())
        except ValueError as error:
            raise ValueError("Map directory escapes the project maps directory") from error
