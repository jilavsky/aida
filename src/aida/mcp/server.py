"""One MCP server: launch a stdio subprocess, discover its tools, execute
calls, and report failures without taking the agent loop down with it.

PLAN.md's diagnostics-as-a-feature requirement ("timing, payload sizes, MIME
types and status recorded for every call") lives in ``ToolCallRecord``.
Failure isolation ("a crashed/hung server errors that one tool call with a
clear layer-naming message; agent loop continues") lives in
``McpServerError`` — callers wrap it as ``AgentError(layer="mcp", ...)``.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from aida.config.settings import McpServerConfig

DEFAULT_CALL_TIMEOUT_SECONDS = 60.0
_STDERR_TAIL_LINES = 200


class McpServerError(Exception):
    """A failure isolated to one server: launch failure, timeout, a call to
    a server that isn't running, or a call to an unknown tool name."""


class StderrCapture:
    """A real-file-backed sink for a stdio server's stderr.

    ``stdio_client()`` hands its ``errlog`` argument straight to
    ``subprocess.Popen(stderr=...)`` (via ``anyio.open_process``), which
    requires a real OS file descriptor — a plain Python object implementing
    only ``write()``/``flush()`` does *not* work here (verified: raises
    ``AttributeError: ... has no attribute 'fileno'`` — the child process
    writes directly to the fd, bypassing Python-level I/O entirely). A
    ``TemporaryFile`` gives us a real fd for the subprocess to inherit while
    still letting us read back what was written, for ``tail()`` diagnostics
    after a crash/hang.
    """

    def __init__(self) -> None:
        # Deliberately not a `with` block: this file must stay open for the
        # capture object's whole lifetime, not just __init__ — closed
        # explicitly via close() when the server stops.
        self._file = tempfile.TemporaryFile(  # noqa: SIM115
            mode="w+", encoding="utf-8", errors="replace"
        )

    def fileno(self) -> int:
        return self._file.fileno()

    def tail(self, n: int = _STDERR_TAIL_LINES) -> list[str]:
        pos = self._file.tell()
        try:
            self._file.seek(0)
            lines = self._file.read().splitlines()
        finally:
            self._file.seek(pos)
        return lines[-n:]

    def close(self) -> None:
        self._file.close()


@dataclass
class ToolCallRecord:
    """Diagnostics for one executed tool call."""

    tool_name: str
    duration_seconds: float
    is_error: bool
    content_types: list[str] = field(default_factory=list)
    payload_bytes: int = 0
    error_message: str | None = None


def _block_payload_size(block: Any) -> int:
    """Rough payload size for diagnostics only — not used for anything that
    needs to be exact, so counting the (base64/plain) text length is fine."""
    data = getattr(block, "data", None)
    if isinstance(data, str):
        return len(data)
    text = getattr(block, "text", None)
    return len(text) if isinstance(text, str) else 0


class McpServerHandle:
    """Owns one stdio MCP server subprocess across its whole lifetime.

    ``stdio_client()`` and ``ClientSession()`` are both async context
    managers; a single ``AsyncExitStack`` holds both open for as long as the
    handle is running, so ``call_tool()`` can be invoked many times without
    re-launching the subprocess on every call.
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.call_timeout_seconds = call_timeout_seconds
        self.stderr: StderrCapture | None = None
        self.calls: list[ToolCallRecord] = []
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._tools: dict[str, Tool] = {}

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_running(self) -> bool:
        return self._session is not None

    async def start(self) -> list[Tool]:
        """Launch the subprocess, initialize the session, and discover
        tools. Idempotent — calling ``start()`` on an already-running handle
        just returns the known tool list. Raises ``McpServerError`` on any
        failure (bad command, crash before handshake, ...); the caller
        isolates this to one server."""
        if self._session is not None:
            return list(self._tools.values())

        stack = AsyncExitStack()
        stderr = StderrCapture()
        try:
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env or None,
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params, errlog=stderr)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tools_result = await session.list_tools()
        except Exception as exc:
            await stack.aclose()
            tail = "\n".join(stderr.tail())
            stderr.close()
            detail = f" — stderr: {tail}" if tail else ""
            raise McpServerError(
                f"mcp server {self.config.name!r} failed to start: {exc}{detail}"
            ) from exc

        self._stack = stack
        self._session = session
        self._tools = {t.name: t for t in tools_result.tools}
        self.stderr = stderr
        return list(self._tools.values())

    async def stop(self) -> None:
        """Tear down the subprocess and session. Safe to call even if the
        handle was never started, or already stopped."""
        if self._stack is not None:
            await self._stack.aclose()
        if self.stderr is not None:
            self.stderr.close()
        self._stack = None
        self._session = None
        self._tools = {}
        self.stderr = None

    async def restart(self) -> list[Tool]:
        """Stop (if running) and start again — recovery path after a
        crashed or wedged server."""
        await self.stop()
        return await self.start()

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        if self._session is None:
            raise McpServerError(f"mcp server {self.config.name!r} is not running")
        if tool_name not in self._tools:
            raise McpServerError(f"mcp server {self.config.name!r} has no tool named {tool_name!r}")

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=self.call_timeout_seconds,
            )
        except TimeoutError as exc:
            self._record_call(tool_name, time.monotonic() - start, is_error=True,
                               error_message=f"timed out after {self.call_timeout_seconds}s")
            raise McpServerError(
                f"mcp server {self.config.name!r} tool {tool_name!r} timed out "
                f"after {self.call_timeout_seconds}s"
            ) from exc
        except Exception as exc:
            self._record_call(tool_name, time.monotonic() - start, is_error=True, error_message=str(exc))
            raise McpServerError(
                f"mcp server {self.config.name!r} tool {tool_name!r} call failed: {exc}"
            ) from exc

        content_types = [getattr(block, "type", "?") for block in result.content]
        payload_bytes = sum(_block_payload_size(block) for block in result.content)
        self._record_call(
            tool_name,
            time.monotonic() - start,
            is_error=bool(result.isError),
            content_types=content_types,
            payload_bytes=payload_bytes,
        )
        return result

    def _record_call(
        self,
        tool_name: str,
        duration_seconds: float,
        *,
        is_error: bool,
        content_types: list[str] | None = None,
        payload_bytes: int = 0,
        error_message: str | None = None,
    ) -> None:
        self.calls.append(
            ToolCallRecord(
                tool_name=tool_name,
                duration_seconds=duration_seconds,
                is_error=is_error,
                content_types=content_types or [],
                payload_bytes=payload_bytes,
                error_message=error_message,
            )
        )


__all__ = ["DEFAULT_CALL_TIMEOUT_SECONDS", "McpServerError", "McpServerHandle", "StderrCapture", "ToolCallRecord"]
