"""Typed models for device automation results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionResult:
    """Normalized result returned by a device driver action."""

    success: bool
    command: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    duration_ms: int = 0
    flow_yaml: str | None = None
    flow_path: str | None = None
    flow_retained: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to the dictionary shape used by older MCP code."""

        return {
            "success": self.success,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "flow_yaml": self.flow_yaml,
            "flow_path": self.flow_path,
            "flow_retained": self.flow_retained,
        }


@dataclass(frozen=True)
class HierarchyResult:
    """A retry-aware hierarchy observation."""

    hierarchy: dict[str, Any]
    success: bool
    error: str | None
    attempts: int
    duration_ms: int
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the dictionary shape used by API adapters."""

        return {
            "hierarchy": self.hierarchy,
            "success": self.success,
            "error": self.error,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class ForegroundInfo:
    """The Android activity currently in the resumed state."""

    success: bool
    package_name: str = ""
    activity_name: str = ""
    error: str | None = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized foreground result."""

        return {
            "success": self.success,
            "package_name": self.package_name,
            "activity_name": self.activity_name,
            "error": self.error,
        }
