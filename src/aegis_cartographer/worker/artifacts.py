"""Export and validate self-contained Aegis map archives."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aegis_cartographer.core.storage.models import MapId
from aegis_cartographer.core.storage.store import MapStore
from aegis_cartographer.core.storage.workspace import ProjectWorkspace

_INCLUDED_DIRECTORIES = ("exports", "screenshots", "maestro_paths")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _default_archive_path(workspace: ProjectWorkspace, map_id: MapId) -> Path:
    safe_name = (
        map_id.app_id.replace("/", "_")
        + "."
        + map_id.platform.value
        + "."
        + map_id.app_version.replace("/", "_")
        + "+"
        + map_id.build_number.replace("/", "_")
        + "."
        + map_id.locale.replace("/", "_")
        + ".aegis-map.zip"
    )
    return workspace.maps_dir / ".archives" / safe_name


def _copy_resource(source: Path, target: Path, map_directory: Path) -> None:
    resolved = source.resolve()
    try:
        resolved.relative_to(map_directory.resolve())
    except ValueError as error:
        raise ValueError(f"Map resource escapes the artifact: {source}") from error
    if source.is_symlink():
        raise ValueError(f"Map resources must not be symlinks: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def export_map_archive(
    workspace: ProjectWorkspace,
    map_id: MapId,
    output: str | Path | None = None,
) -> Path:
    """Create an atomic, portable zip archive for one project-local map."""

    map_directory = workspace.map_directory(map_id)
    target = Path(output).expanduser() if output is not None else _default_archive_path(workspace, map_id)
    if not target.is_absolute():
        target = workspace.root / target
    if target.is_symlink():
        raise ValueError("Map archive output must not be a symlink")
    target = target.resolve()
    try:
        target.relative_to(map_directory.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Map archive output cannot be inside the map artifact directory")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".zip.tmp",
        dir=str(target.parent),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        store = MapStore.open(workspace, map_id, readonly=True)
        try:
            with tempfile.TemporaryDirectory(prefix="aegis-map-export-") as temporary_dir:
                stage = Path(temporary_dir)
                database_stage = stage / "map.sqlite"
                destination = sqlite3.connect(database_stage)
                try:
                    store.connection.backup(destination)
                finally:
                    destination.close()
                shutil.copy2(map_directory / "manifest.json", stage / "manifest.json")
                pages = [page.to_dict() for page in store.list_page_summaries()]
                (stage / "pages.json").write_text(
                    json.dumps(
                        {
                            "map_id": map_id.to_dict(),
                            "page_count": len(pages),
                            "pages": pages,
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                for directory_name in _INCLUDED_DIRECTORIES:
                    directory = map_directory / directory_name
                    if not directory.exists():
                        continue
                    for source in directory.rglob("*"):
                        if source.is_file():
                            relative = source.relative_to(map_directory)
                            _copy_resource(source, stage / relative, map_directory)

                with zipfile.ZipFile(
                    temporary_path,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                ) as archive:
                    for source in sorted(stage.rglob("*")):
                        if not source.is_file():
                            continue
                        relative = source.relative_to(stage).as_posix()
                        info = zipfile.ZipInfo(
                            filename=f"aegis-map/{relative}",
                            date_time=_FIXED_ZIP_TIME,
                        )
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o600 << 16
                        info.create_system = 3
                        with (
                            source.open("rb") as source_stream,
                            archive.open(info, "w") as archive_stream,
                        ):
                            shutil.copyfileobj(source_stream, archive_stream, length=1024 * 1024)
                    archive.comment = (
                        "Aegis Cartographer portable map; created_at="
                        + datetime.now(timezone.utc).isoformat(timespec="seconds")
                    ).encode("utf-8")
        finally:
            store.close()
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, target)
    except BaseException:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return target


def validate_map_archive(archive_path: str | Path) -> dict[str, Any]:
    """Validate archive structure, manifest identity, SQLite integrity, and FKs."""

    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Map archive not found: {path}")
    if path.is_symlink():
        raise ValueError("Map archive must not be a symlink")

    with tempfile.TemporaryDirectory(prefix="aegis-map-validate-") as temporary_dir:
        root = Path(temporary_dir)
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if len(names) > 10000:
                raise ValueError("Archive contains too many members")
            if "aegis-map/manifest.json" not in names:
                raise ValueError("Archive is missing aegis-map/manifest.json")
            if "aegis-map/map.sqlite" not in names:
                raise ValueError("Archive is missing aegis-map/map.sqlite")
            for name in names:
                normalized = Path(name)
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise ValueError(f"Unsafe archive member: {name}")
                info = archive.getinfo(name)
                file_mode = (info.external_attr >> 16) & 0o170000
                if file_mode == 0o120000:
                    raise ValueError(f"Archive member must not be a symlink: {name}")
                if info.file_size > 512 * 1024 * 1024:
                    raise ValueError(f"Archive member is too large: {name}")
            total_size = sum(archive.getinfo(name).file_size for name in names)
            if total_size > 2 * 1024 * 1024 * 1024:
                raise ValueError("Archive uncompressed size exceeds 2 GiB")
            if len(names) != len(set(names)):
                raise ValueError("Archive contains duplicate member names")
            archive.extractall(root)

        artifact = root / "aegis-map"
        import json

        manifest_value = json.loads(
            (artifact / "manifest.json").read_text(encoding="utf-8")
        )
        from aegis_cartographer.core.storage.models import MapManifest

        manifest = MapManifest.from_dict(manifest_value)
        connection = sqlite3.connect(artifact / "map.sqlite")
        try:
            integrity = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            map_identity = connection.execute(
                "SELECT value FROM map_metadata WHERE key = 'map_id'"
            ).fetchone()
            state_count = int(
                connection.execute("SELECT COUNT(*) FROM screen_states").fetchone()[0]
            )
            element_count = int(
                connection.execute("SELECT COUNT(*) FROM elements").fetchone()[0]
            )
        finally:
            connection.close()

    valid = integrity == "ok" and not foreign_keys and map_identity is not None
    return {
        "valid": valid,
        "archive": str(path),
        "map_id": manifest.map_id.cache_key,
        "sqlite_integrity": integrity,
        "foreign_key_violations": len(foreign_keys),
        "screen_states": state_count,
        "elements": element_count,
    }
