"""Map identity selection helpers for CLI and worker entry points."""

from __future__ import annotations

from dataclasses import dataclass

from aegis_cartographer.core.models import Platform
from aegis_cartographer.core.storage.models import MapId
from aegis_cartographer.worker.config import ProjectConfig


@dataclass(frozen=True)
class MapSelection:
    """Explicit map identity inputs parsed from CLI options."""

    selector: str | None = None
    app_id: str | None = None
    platform: Platform | None = None
    app_version: str | None = None
    build_number: str | None = None
    locale: str | None = None


def parse_map_selector(value: str) -> MapId:
    """Parse ``app@platform:version+build/locale`` into a MapId."""

    if value.count("@") != 1 or ":" not in value or "+" not in value or "/" not in value:
        raise ValueError(
            "Map selector must use app@platform:version+build/locale"
        )
    app_id, remainder = value.split("@", 1)
    platform_value, version_and_rest = remainder.split(":", 1)
    app_version, build_and_locale = version_and_rest.rsplit("+", 1)
    build_number, locale = build_and_locale.split("/", 1)
    try:
        platform = Platform(platform_value.lower())
    except ValueError as error:
        raise ValueError(f"Unsupported map platform: {platform_value}") from error
    return MapId(
        app_id=app_id,
        platform=platform,
        app_version=app_version,
        build_number=build_number,
        locale=locale,
    )


def resolve_map_id(config: ProjectConfig | None, selection: MapSelection) -> MapId:
    """Resolve a map from an explicit selector, overrides, or project config."""

    if selection.selector is not None:
        return parse_map_selector(selection.selector)

    values = {
        "app_id": selection.app_id,
        "platform": selection.platform,
        "app_version": selection.app_version,
        "build_number": selection.build_number,
        "locale": selection.locale,
    }
    if any(value is not None for value in values.values()):
        if config is None:
            config = ProjectConfig()
        return MapId(
            app_id=selection.app_id or config.app_id,
            platform=selection.platform or config.platform,
            app_version=selection.app_version or config.app_version,
            build_number=selection.build_number or config.build_number,
            locale=selection.locale or config.locale,
        )

    if config is None:
        raise ValueError("No map selected and no project config exists")
    return config.default_map_id()
