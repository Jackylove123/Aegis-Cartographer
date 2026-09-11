"""Persistent Maestro MCP device driver.

The normal CLI driver starts a new Maestro process for every hierarchy read and
flow execution. This adapter starts one ``maestro mcp`` process per exploration
worker and reuses it for the whole run.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aegis_cartographer.device.maestro_driver import MaestroDriver
from aegis_cartographer.device.models import ActionResult, HierarchyResult


class MaestroMCPError(RuntimeError):
    """Raised when the persistent Maestro MCP service returns an error."""


class _PersistentMcpSession:
    """Keep one MCP stdio session alive on a private event-loop thread."""

    def __init__(
        self,
        *,
        command: str,
        args: Sequence[str],
        start_timeout: float = 10.0,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._start_timeout = start_timeout
        self._session: ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the MCP server and initialize the client session."""

        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="aegis-maestro-mcp",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(self._start_timeout):
            error = self._start_error
            self.close()
            raise TimeoutError(
                f"Maestro MCP server did not initialize within "
                f"{self._start_timeout:g}s"
                + (f": {error}" if error else "")
            )
        if self._start_error is not None:
            raise RuntimeError("Maestro MCP server failed to initialize") from (
                self._start_error
            )

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        parameters = StdioServerParameters(
            command=self._command,
            args=self._args,
        )
        try:
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await stop_event.wait()
        except BaseException as error:
            self._start_error = error
            self._ready.set()

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: float,
    ) -> Any:
        """Call an MCP tool from synchronous Aegis code."""

        session = self._session
        loop = self._loop
        if session is None or loop is None or not self._ready.is_set():
            raise MaestroMCPError("Maestro MCP session is not ready")
        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(name, dict(arguments), read_timeout_seconds=timeout),
            loop,
        )
        try:
            return future.result(timeout)
        except TimeoutError as error:
            future.cancel()
            raise MaestroMCPError(
                f"Maestro MCP tool {name!r} timed out after {timeout:g}s"
            ) from error

    def close(self) -> None:
        """Signal the persistent session to exit and wait briefly for cleanup."""

        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive() and self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)


class MaestroMcpDriver(MaestroDriver):
    """Maestro driver that reuses one MCP process for a complete run."""

    def __init__(
        self,
        *args: Any,
        maestro_path: str | None = None,
        mcp_no_viewer: bool = True,
        mcp_start_timeout: float = 10.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, maestro_path=maestro_path, **kwargs)
        self.mcp_no_viewer = mcp_no_viewer
        self.mcp_start_timeout = mcp_start_timeout
        self._session = _PersistentMcpSession(
            command=self.maestro_bin,
            args=["mcp"] + (["--no-viewer"] if mcp_no_viewer else []),
            start_timeout=mcp_start_timeout,
        )

    def connect(self) -> None:
        """Start the persistent Maestro MCP server."""

        self._session.start()

    def close(self) -> None:
        """Stop the persistent Maestro MCP server."""

        self._session.close()

    def _text_content(self, result: Any) -> str:
        chunks: list[str] = []
        for content in getattr(result, "content", ()) or ():
            if getattr(content, "type", "") == "text":
                chunks.append(str(getattr(content, "text", "")))
        return "\n".join(chunks)

    def _raise_for_error(self, result: Any, operation: str) -> None:
        if getattr(result, "isError", False):
            raise MaestroMCPError(
                f"Maestro MCP {operation} failed: {self._text_content(result)}"
            )

    def _json_payload(self, result: Any) -> dict[str, Any]:
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            return structured
        text = self._text_content(result)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise MaestroMCPError(
                "Maestro MCP inspect_screen returned invalid JSON"
            ) from error
        if not isinstance(payload, dict) or not payload:
            raise MaestroMCPError("Maestro MCP inspect_screen returned an empty payload")
        return payload

    def get_hierarchy_result(self) -> HierarchyResult:
        """Read the hierarchy through the persistent MCP session."""

        started = time.monotonic()
        try:
            result = self._session.call_tool(
                "inspect_screen",
                {"device_id": self.device_id},
                timeout=self.hierarchy_timeout,
            )
            self._raise_for_error(result, "inspect_screen")
            hierarchy = self._json_payload(result)
            return HierarchyResult(
                hierarchy=hierarchy,
                success=True,
                error=None,
                attempts=1,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except (MaestroMCPError, TimeoutError) as error:
            return HierarchyResult(
                hierarchy={},
                success=False,
                error=f"maestro_mcp_{type(error).__name__}",
                attempts=1,
                duration_ms=int((time.monotonic() - started) * 1000),
                stderr=str(error),
            )

    def execute_flow(
        self,
        commands: Sequence[Mapping[str, Any] | str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute inline YAML through the persistent MCP session."""

        started = time.monotonic()
        resolved_timeout = self.action_timeout if timeout is None else timeout
        try:
            flow_yaml = self._render_flow(commands)
        except (OSError, ValueError) as error:
            return self._failure(str(error), ["maestro-mcp", "run"])

        try:
            result = self._session.call_tool(
                "run",
                {
                    "device_id": self.device_id,
                    "yaml": flow_yaml,
                },
                timeout=resolved_timeout,
            )
        except (MaestroMCPError, TimeoutError) as error:
            return ActionResult(
                success=False,
                command=("maestro-mcp", "run"),
                stderr=str(error),
                error="maestro_mcp_error",
                duration_ms=int((time.monotonic() - started) * 1000),
                flow_yaml=flow_yaml,
                flow_retained=True,
            ).to_dict()

        self._raise_for_error(result, "run")
        return ActionResult(
            success=True,
            command=("maestro-mcp", "run"),
            stdout=self._text_content(result),
            stderr="",
            exit_code=0,
            error=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            flow_yaml=flow_yaml,
            flow_retained=False,
        ).to_dict()

    def wait_until_stable(
        self,
        *,
        timeout: float | None = None,
        interval: float = 0.3,
        stable_count: int = 2,
    ) -> dict[str, Any]:
        """Use one MCP hierarchy read; animation waiting is already in the flow."""

        resolved_timeout = self.stability_timeout if timeout is None else timeout
        if resolved_timeout <= 0 or interval <= 0 or stable_count < 1:
            return self._failure("timeout, interval, and stable_count are invalid")

        result = self.get_hierarchy_result()
        if not result.success or not result.hierarchy:
            return self._failure(result.error or "maestro_mcp_hierarchy_failed")

        try:
            from aegis_cartographer.core.hierarchy import compute_screen_signature

            signature = compute_screen_signature(result.hierarchy)
        except ValueError as error:
            return self._failure(f"invalid hierarchy: {error}")

        return {
            "success": True,
            "stable": True,
            "attempts": 1,
            "empty_observations": 0,
            "state_id": signature.state_id,
            "hierarchy": result.hierarchy,
            "last_hierarchy_error": None,
        }
