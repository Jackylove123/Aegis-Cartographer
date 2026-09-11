"""Reliable Maestro CLI adapter.

The driver deliberately renders YAML from a constrained command model. Complex
values are emitted as JSON flow mappings (a YAML subset) and simple commands are
restricted to identifier-like allowlisted names. User-visible text is therefore
never interpolated into an unsafe plain YAML scalar.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aegis_cartographer.core.hierarchy import compute_screen_signature
from aegis_cartographer.device.models import (
    ActionResult,
    ForegroundInfo,
    HierarchyResult,
)

_DEFAULT_MAESTRO_PATHS = (
    os.path.expanduser("~/.maestro/maestro/bin/maestro"),
    "/usr/local/bin/maestro",
)
_DEFAULT_ADB_PATHS = (
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/usr/local/bin/adb",
    "/opt/homebrew/bin/adb",
)
_SIMPLE_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SIMPLE_COMMANDS = {
    "waitForAnimationToEnd",
    "hideKeyboard",
    "scroll",
    "back",
}
_DIRECTIONS = {"UP", "DOWN", "LEFT", "RIGHT"}
_FlowCommand = Mapping[str, Any] | str


def _positive(value: float, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _non_negative(value: float, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")


def _find_maestro_binary() -> str:
    """Locate the Maestro executable without invoking a shell."""

    configured = os.environ.get("AEGIS_MAESTRO_PATH", "").strip()
    if configured:
        if not os.path.isfile(configured):
            raise FileNotFoundError(f"AEGIS_MAESTRO_PATH is not a file: {configured}")
        return configured

    for candidate in _DEFAULT_MAESTRO_PATHS:
        if os.path.isfile(candidate):
            return candidate

    discovered = shutil.which("maestro")
    if discovered:
        return discovered

    raise FileNotFoundError(
        "Maestro CLI was not found. Install Maestro or set AEGIS_MAESTRO_PATH."
    )


def _find_adb_binary() -> str:
    """Locate adb without invoking a shell."""

    configured = os.environ.get("AEGIS_ADB_PATH", "").strip()
    if configured:
        if not os.path.isfile(configured):
            raise FileNotFoundError(f"AEGIS_ADB_PATH is not a file: {configured}")
        return configured
    for candidate in _DEFAULT_ADB_PATHS:
        if os.path.isfile(candidate):
            return candidate
    discovered = shutil.which("adb")
    if discovered:
        return discovered
    raise FileNotFoundError("adb was not found. Set AEGIS_ADB_PATH.")


class MaestroDriver:
    """Maestro CLI device driver with typed, structured action results."""

    def __init__(
        self,
        device_id: str = "",
        app_id: str = "",
        maestro_path: str | None = None,
        adb_path: str | None = None,
        command_timeout: float = 15.0,
        *,
        action_timeout: float = 40.0,
        hierarchy_timeout: float = 20.0,
        hierarchy_retries: int = 3,
        hierarchy_retry_backoff_ms: int = 500,
        animation_wait_timeout_ms: int = 3000,
        stability_timeout: float = 10.0,
        back_timeout: float = 15.0,
        launch_timeout: float = 30.0,
        flow_dir: str | None = None,
        keep_successful_flows: bool = False,
    ) -> None:
        self.device_id = device_id
        self.app_id = app_id
        self.maestro_bin = maestro_path or _find_maestro_binary()
        self.adb_bin = adb_path
        self.command_timeout = command_timeout
        self.action_timeout = action_timeout
        self.hierarchy_timeout = hierarchy_timeout
        self.hierarchy_retries = hierarchy_retries
        self.hierarchy_retry_backoff_ms = hierarchy_retry_backoff_ms
        self.animation_wait_timeout_ms = animation_wait_timeout_ms
        self.stability_timeout = stability_timeout
        self.back_timeout = back_timeout
        self.launch_timeout = launch_timeout
        self.flow_dir = flow_dir or os.path.join(tempfile.gettempdir(), "aegis_flows")
        self.keep_successful_flows = keep_successful_flows

        _positive(self.command_timeout, "command_timeout")
        _positive(self.action_timeout, "action_timeout")
        _positive(self.hierarchy_timeout, "hierarchy_timeout")
        _positive(self.animation_wait_timeout_ms, "animation_wait_timeout_ms")
        _positive(self.stability_timeout, "stability_timeout")
        _positive(self.back_timeout, "back_timeout")
        _positive(self.launch_timeout, "launch_timeout")
        if self.hierarchy_retries < 1:
            raise ValueError("hierarchy_retries must be at least 1")
        _non_negative(self.hierarchy_retry_backoff_ms, "hierarchy_retry_backoff_ms")
        self.last_hierarchy_result: HierarchyResult | None = None

    def _animation_wait_command(self) -> _FlowCommand:
        return {
            "waitForAnimationToEnd": {"timeout": self.animation_wait_timeout_ms},
        }

    @staticmethod
    def _hierarchy_error(result: Mapping[str, Any]) -> str:
        if result.get("success"):
            return "hierarchy_invalid_payload"
        raw = str(result.get("error") or "hierarchy_cli_error")
        return f"hierarchy_{raw}"

    def _build_cli_command(self, args: Sequence[str]) -> list[str]:
        """Build a Maestro command, adding device selection only to test flows."""

        command = [self.maestro_bin, *args]
        # `maestro hierarchy` does not accept the test-only --device option in
        # Maestro versions targeted by this driver.
        if self.device_id and args and args[0] == "test":
            command.extend(["--device", self.device_id])
        return command

    def _run_cli(
        self,
        args: Sequence[str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run a Maestro CLI command and normalize all failure modes."""

        command = self._build_cli_command(args)

        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.command_timeout,
                check=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "success": completed.returncode == 0,
                "command": command,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "exit_code": completed.returncode,
                "error": None if completed.returncode == 0 else "maestro_exit_error",
                "duration_ms": duration_ms,
            }
        except subprocess.TimeoutExpired as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "success": False,
                "command": command,
                "stdout": error.stdout or "" if isinstance(error.stdout, str) else "",
                "stderr": error.stderr or "" if isinstance(error.stderr, str) else "",
                "exit_code": None,
                "error": "timeout",
                "duration_ms": duration_ms,
            }
        except OSError as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "success": False,
                "command": command,
                "stdout": "",
                "stderr": str(error),
                "exit_code": None,
                "error": "os_error",
                "duration_ms": duration_ms,
            }

    def _run_adb(
        self,
        args: Sequence[str],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        command = [self.adb_bin]
        if self.device_id:
            command.extend(["-s", self.device_id])
        command.extend(args)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(error),
                "error": "adb_error",
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        return {
            "success": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": None if completed.returncode == 0 else "adb_exit_error",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def get_foreground_activity(self) -> ForegroundInfo:
        """Read the resumed Android activity and parse its package/activity."""
        if not self.adb_bin:
            self.adb_bin = _find_adb_binary()
        result = self._run_adb(
            ["shell", "dumpsys", "activity", "activities"],
            timeout=max(5.0, min(self.command_timeout, 15.0)),
        )
        if not result.get("success"):
            return ForegroundInfo(
                success=False,
                error=str(result.get("error") or "adb_error"),
                stderr=str(result.get("stderr") or ""),
            )
        stdout = str(result.get("stdout") or "")
        match = re.search(
            r"mResumedActivity:\s+ActivityRecord\{[^ ]+ u0 ([^/\s]+)/(\S+)",
            stdout,
        )
        if match is None:
            match = re.search(
                r"ResumedActivity:\s+ActivityRecord\{[^ ]+ u0 ([^/\s]+)/(\S+)",
                stdout,
            )
        if match is None:
            return ForegroundInfo(
                success=False,
                error="resumed_activity_not_found",
                stdout=stdout,
            )
        return ForegroundInfo(
            success=True,
            package_name=match.group(1),
            activity_name=match.group(2),
        )

    @staticmethod
    def _json_fragment(value: Any) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"Flow command is not safely serializable: {error}") from error

    def _render_flow(self, commands: Sequence[_FlowCommand]) -> str:
        lines: list[str] = []
        if self.app_id:
            lines.append(self._json_fragment({"appId": self.app_id}))
        if self.app_id or commands:
            lines.append("---")

        for command in commands:
            if isinstance(command, str):
                if command not in _SIMPLE_COMMANDS or not _SIMPLE_COMMAND_RE.fullmatch(command):
                    raise ValueError(f"Unsafe or unsupported simple command: {command!r}")
                lines.append(f"- {command}")
                continue
            if not isinstance(command, Mapping):
                raise ValueError("A flow command must be a mapping or allowlisted string")
            if not all(isinstance(key, str) for key in command):
                raise ValueError("Flow command keys must be strings")
            lines.append(f"- {self._json_fragment(dict(command))}")

        return "\n".join(lines) + "\n"

    def _write_flow(self, yaml_content: str) -> str:
        flow_root = Path(self.flow_dir)
        flow_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, path = tempfile.mkstemp(
            prefix="aegis-flow-",
            suffix=".yaml",
            dir=str(flow_root),
            text=True,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(yaml_content)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return path

    @staticmethod
    def _failure(error: str, command: Sequence[str] = ()) -> dict[str, Any]:
        return ActionResult(success=False, command=tuple(command), error=error).to_dict()

    def execute_flow(
        self,
        commands: Sequence[_FlowCommand],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a generated Maestro flow with a unique temporary file."""

        try:
            flow_yaml = self._render_flow(commands)
            flow_path = self._write_flow(flow_yaml)
        except (OSError, ValueError) as error:
            return self._failure(str(error), ["maestro", "test"])

        result = self._run_cli(["test", flow_path], timeout=timeout)
        retained = False
        if result["success"] and not self.keep_successful_flows:
            try:
                os.unlink(flow_path)
            except OSError:
                retained = True
        else:
            # Keep failed flows so a device run can be reproduced during triage.
            retained = True

        return ActionResult(
            success=bool(result["success"]),
            command=tuple(result["command"]),
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            error=result["error"],
            duration_ms=result["duration_ms"],
            flow_yaml=flow_yaml,
            flow_path=flow_path,
            flow_retained=retained,
        ).to_dict()

    def get_hierarchy(self) -> dict[str, Any]:
        """Return the current hierarchy, or an empty object on failure."""

        result = self.get_hierarchy_result()
        self.last_hierarchy_result = result
        return result.hierarchy

    def get_hierarchy_result(self) -> HierarchyResult:
        """Fetch and validate the current hierarchy with bounded retries."""

        started = time.monotonic()
        attempts = 0
        error: str | None = None
        stderr = ""
        for attempt in range(1, self.hierarchy_retries + 1):
            attempts = attempt
            result = self._run_cli(["hierarchy"], timeout=self.hierarchy_timeout)
            stderr = str(result.get("stderr") or "")
            if result.get("success"):
                try:
                    hierarchy = json.loads(result["stdout"])
                except (json.JSONDecodeError, TypeError):
                    error = "hierarchy_invalid_json"
                else:
                    if isinstance(hierarchy, dict) and hierarchy:
                        return HierarchyResult(
                            hierarchy=hierarchy,
                            success=True,
                            error=None,
                            attempts=attempts,
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                    error = "hierarchy_empty"
            else:
                error = self._hierarchy_error(result)
            if attempt < self.hierarchy_retries:
                time.sleep(self.hierarchy_retry_backoff_ms / 1000.0)

        return HierarchyResult(
            hierarchy={},
            success=False,
            error=error or "hierarchy_unknown_error",
            attempts=attempts,
            duration_ms=int((time.monotonic() - started) * 1000),
            stderr=stderr,
        )

    @staticmethod
    def _selector(
        *,
        element_id: str | None = None,
        text: str | None = None,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        bounds: str | None = None,
        occurrence_index: int | None = None,
    ) -> dict[str, Any] | None:
        selector: dict[str, Any] | None = None
        if element_id:
            selector = {"id": element_id}
        elif text:
            selector = {"text": text}
        elif label:
            selector = {"label": label}
        elif point:
            selector = {"point": f"{point[0]},{point[1]}"}
        elif bounds:
            numbers = [int(value) for value in re.findall(r"-?\d+", bounds)]
            if len(numbers) < 4:
                return None
            left, top, right, bottom = numbers[:4]
            if right <= left or bottom <= top:
                return None
            selector = {
                "point": f"{(left + right) // 2},{(top + bottom) // 2}",
            }

        if selector is None:
            return None
        if occurrence_index is not None:
            if occurrence_index < 1:
                raise ValueError("occurrence_index must be 1-based")
            selector["index"] = occurrence_index - 1
        return selector

    def _press_action(
        self,
        command_name: str,
        timeout: float,
        **selector_args: Any,
    ) -> dict[str, Any]:
        try:
            selector = self._selector(**selector_args)
        except ValueError as error:
            return self._failure(str(error), ["maestro", "test"])
        if selector is None:
            return self._failure("A valid element_id, text, label, point, or bounds is required")
        return self.execute_flow(
            [
                {command_name: selector},
                self._animation_wait_command(),
            ],
            timeout=timeout,
        )

    def tap_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Tap an element by stable selector, label, point, or bounds center."""

        return self._press_action(
            "tapOn",
            self.action_timeout if timeout is None else timeout,
            element_id=element_id,
            text=text,
            bounds=bounds,
            label=label,
            point=point,
            occurrence_index=occurrence_index,
        )

    def long_press_on(
        self,
        element_id: str | None = None,
        text: str | None = None,
        bounds: str | None = None,
        *,
        label: str | None = None,
        point: tuple[int, int] | None = None,
        occurrence_index: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Long-press an element using Maestro's longPressOn command."""

        return self._press_action(
            "longPressOn",
            self.action_timeout if timeout is None else timeout,
            element_id=element_id,
            text=text,
            bounds=bounds,
            label=label,
            point=point,
            occurrence_index=occurrence_index,
        )

    def swipe(
        self,
        direction: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Swipe in one of the four canonical directions."""

        normalized = direction.upper()
        if normalized not in _DIRECTIONS:
            return self._failure(f"direction must be one of {sorted(_DIRECTIONS)}")
        return self.execute_flow(
            [{"swipe": normalized}, self._animation_wait_command()],
            timeout=self.action_timeout if timeout is None else timeout,
        )

    def scroll(
        self,
        direction: str = "DOWN",
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Scroll using Maestro's scroll command."""

        normalized = direction.upper()
        if normalized not in _DIRECTIONS:
            return self._failure(f"direction must be one of {sorted(_DIRECTIONS)}")
        # Maestro's portable direction primitive is swipe. This also avoids
        # platform-specific scroll command options in the public driver API.
        return self.swipe(normalized, timeout=timeout)

    def back(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Press the Android BACK key."""

        return self.execute_flow(
            [{"pressKey": "BACK"}, self._animation_wait_command()],
            timeout=self.back_timeout if timeout is None else timeout,
        )

    def hide_keyboard(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Hide the keyboard when Maestro supports it for the platform."""

        return self.execute_flow(
            ["hideKeyboard", self._animation_wait_command()],
            timeout=self.back_timeout if timeout is None else timeout,
        )

    def stop_app(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Stop the configured application."""

        if not self.app_id:
            return self._failure("app_id is not configured")
        return self.execute_flow(
            [{"stopApp": self.app_id}],
            timeout=self.launch_timeout if timeout is None else timeout,
        )

    def launch_app(
        self,
        *,
        restart: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Launch the configured application, optionally restarting it first."""

        if not self.app_id:
            return self._failure("app_id is not configured")
        commands: list[_FlowCommand] = []
        if restart:
            commands.append({"stopApp": self.app_id})
        commands.extend(
            [
                {"launchApp": self.app_id},
                self._animation_wait_command(),
            ]
        )
        return self.execute_flow(
            commands,
            timeout=self.launch_timeout if timeout is None else timeout,
        )

    def restart_app(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Restart the configured application."""

        return self.launch_app(restart=True, timeout=timeout)

    def input_text(
        self,
        text: str,
        element_id: str | None = None,
        *,
        hide_keyboard: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Optionally focus an element and enter text."""

        commands: list[_FlowCommand] = []
        if element_id:
            commands.append({"tapOn": {"id": element_id}})
        commands.append({"inputText": text})
        if hide_keyboard:
            commands.append("hideKeyboard")
        return self.execute_flow(
            commands,
            timeout=self.action_timeout if timeout is None else timeout,
        )

    def wait_until_stable(
        self,
        *,
        timeout: float | None = None,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        """Wait until consecutive structural signatures stop changing."""

        resolved_timeout = self.stability_timeout if timeout is None else timeout
        if resolved_timeout <= 0 or interval <= 0 or stable_count < 2:
            return self._failure("timeout, interval, and stable_count are invalid")

        deadline = time.monotonic() + resolved_timeout
        last_state_id: str | None = None
        stable_occurrences = 0
        attempts = 0
        empty_observations = 0
        last_hierarchy: dict[str, Any] = {}
        last_hierarchy_error: str | None = None

        while time.monotonic() < deadline:
            attempts += 1
            hierarchy = self.get_hierarchy()
            if not hierarchy:
                empty_observations += 1
                last_state_id = None
                stable_occurrences = 0
                last_hierarchy_error = (
                    self.last_hierarchy_result.error
                    if self.last_hierarchy_result is not None
                    else None
                )
            else:
                last_hierarchy_error = None
                try:
                    signature = compute_screen_signature(hierarchy)
                except ValueError:
                    empty_observations += 1
                    last_state_id = None
                    stable_occurrences = 0
                else:
                    last_hierarchy = hierarchy
                    if signature.state_id == last_state_id:
                        stable_occurrences += 1
                    else:
                        last_state_id = signature.state_id
                        stable_occurrences = 1

            if stable_occurrences >= stable_count:
                return {
                    "success": True,
                    "stable": True,
                    "attempts": attempts,
                    "empty_observations": empty_observations,
                    "state_id": last_state_id,
                    "hierarchy": last_hierarchy,
                    "last_hierarchy_error": last_hierarchy_error,
                }
            time.sleep(interval)

        return {
            "success": False,
            "stable": False,
            "attempts": attempts,
            "empty_observations": empty_observations,
            "state_id": last_state_id,
            "hierarchy": last_hierarchy,
            "error": "timeout",
            "last_hierarchy_error": last_hierarchy_error,
        }
