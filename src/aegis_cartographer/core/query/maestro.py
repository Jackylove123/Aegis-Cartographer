"""Safe Maestro flow rendering for map-derived commands."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

_SIMPLE_COMMANDS = {
    "waitForAnimationToEnd",
    "hideKeyboard",
    "scroll",
    "back",
}
_COMMAND_KEYS = {
    "tapOn",
    "longPressOn",
    "swipe",
    "scroll",
    "pressKey",
    "launchApp",
    "stopApp",
    "inputText",
    "waitForAnimationToEnd",
    "hideKeyboard",
    "back",
}


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"Maestro command is not safely serializable: {error}") from error


def validate_maestro_commands(commands: Sequence[Mapping[str, Any] | str]) -> None:
    """Validate map-derived commands before they can become a flow."""

    for command in commands:
        if isinstance(command, str):
            if command not in _SIMPLE_COMMANDS:
                raise ValueError(f"Unsupported simple Maestro command: {command!r}")
            continue
        if not isinstance(command, Mapping):
            raise ValueError("A Maestro command must be a mapping or allowlisted string")
        if len(command) != 1:
            raise ValueError("A mapped Maestro command must contain exactly one key")
        key = next(iter(command))
        if key not in _COMMAND_KEYS:
            raise ValueError(f"Unsupported Maestro command: {key!r}")
        _json(dict(command))


def render_maestro_flow(
    commands: Sequence[Mapping[str, Any] | str],
    *,
    app_id: str = "",
) -> str:
    """Render commands as a constrained YAML subset without unsafe interpolation."""

    validate_maestro_commands(commands)
    lines: list[str] = []
    if app_id:
        lines.append(_json({"appId": app_id}))
    lines.append("---")
    for command in commands:
        if isinstance(command, str):
            lines.append(f"- {command}")
        else:
            lines.append(f"- {_json(dict(command))}")
    return "\n".join(lines) + "\n"
