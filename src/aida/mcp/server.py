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
import contextlib
import tempfile
import time
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
    """Diagnostics for one executed tool call.

    ``arguments`` and ``content_preview`` (Phase 7) exist for the MCP
    management UI's raw result inspector — "click any tool call in the log
    -> exact MCP response (JSON, base64 lengths noted), copyable"
    (planning/phase07_mcp_management.md). Built directly from the raw
    content blocks here in ``McpServerHandle.call_tool``, independent of
    ``aida.mcp.results.convert_result``/the artifact-event pipeline, so
    inspecting a call needs no changes anywhere else — this is deliberately
    the *only* place raw MCP wire content is ever kept around.
    """

    tool_name: str
    duration_seconds: float
    is_error: bool
    content_types: list[str] = field(default_factory=list)
    payload_bytes: int = 0
    error_message: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    content_preview: list[dict[str, Any]] = field(default_factory=list)
    #: ``time.monotonic()`` at record time — comparable across every
    #: ``McpServerHandle`` in the same process (unlike wall-clock time, it
    #: never jumps), which is what lets ``McpManager.recent_calls`` merge
    #: several servers' call logs into one correctly time-ordered list.
    recorded_at: float = field(default_factory=time.monotonic)


#: How much of a text/JSON block's content the raw inspector keeps —
#: enough to actually debug a real response, capped so one huge document
#: doesn't bloat every in-memory call record.
_PREVIEW_TEXT_CHARS = 2000


def _block_payload_size(block: Any) -> int:
    """Rough payload size for diagnostics only — not used for anything that
    needs to be exact, so counting the (base64/plain) text length is fine."""
    data = getattr(block, "data", None)
    if isinstance(data, str):
        return len(data)
    text = getattr(block, "text", None)
    return len(text) if isinstance(text, str) else 0


def _block_preview(block: Any) -> dict[str, Any]:
    """One content block -> a JSON-safe preview dict for the raw inspector.
    Never includes full raw base64 — only its length, per the "base64
    lengths noted" requirement — and truncates long text/JSON so the
    in-memory log stays bounded."""
    block_type = getattr(block, "type", "?")

    if block_type == "text":
        text = getattr(block, "text", "") or ""
        return {"type": "text", "text": text[:_PREVIEW_TEXT_CHARS]}

    if block_type == "image":
        return {
            "type": "image",
            "mime_type": getattr(block, "mimeType", None),
            "base64_length": len(getattr(block, "data", "") or ""),
        }

    if block_type == "audio":
        return {
            "type": "audio",
            "mime_type": getattr(block, "mimeType", None),
            "base64_length": len(getattr(block, "data", "") or ""),
        }

    if block_type == "resource_link":
        return {"type": "resource_link", "name": getattr(block, "name", None), "uri": str(getattr(block, "uri", ""))}

    if block_type == "resource":
        resource = getattr(block, "resource", None)
        text = getattr(resource, "text", None)
        if text is not None:
            return {"type": "resource", "mime_type": getattr(resource, "mimeType", None), "text": text[:_PREVIEW_TEXT_CHARS]}
        blob = getattr(resource, "blob", None)
        return {
            "type": "resource",
            "mime_type": getattr(resource, "mimeType", None),
            "base64_length": len(blob or ""),
        }

    return {"type": block_type}


class McpServerHandle:
    """Owns one stdio MCP server subprocess across its whole lifetime.

    ``stdio_client()``/``ClientSession()`` are async context managers whose
    ``anyio`` cancel scopes are *task-affine*: whichever ``asyncio`` Task
    calls ``__aenter__`` must be the same Task that later calls
    ``__aexit__``, or anyio raises ``RuntimeError("Attempted to exit a
    cancel scope that isn't the current task's current cancel scope")`` —
    verified the hard way (Phase 7): every top-level ``aida.cli.chat``/
    ``asyncio.run`` call happens to run start and stop within one Task, so
    this was invisible there, but ``aida.ui.qt.bridge.ChatBridge`` schedules
    *every* action (``start_mcp_server``, ``stop_mcp_server``, ...) as its
    own independent coroutine via ``asyncio.run_coroutine_threadsafe`` —
    i.e., its own Task — which is exactly the shape Phase 7's live
    per-server start/stop needs and exactly what broke.

    So both context managers are entered and exited from **one dedicated
    background task** (``self._serve_task``, started by ``start()`` and
    joined by ``stop()``) rather than directly inside whatever task called
    ``start()``/``stop()`` — those two just signal the serving task
    (``asyncio.Event``s) and wait for it, which has no task-affinity
    requirement of its own. ``call_tool()`` needs no such care: ordinary
    method calls on ``self._session`` aren't task-bound, only entering/
    exiting its context manager is.
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
        self._tools: dict[str, Tool] = {}
        self._serve_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._start_error: McpServerError | None = None

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

        self._stop_event = asyncio.Event()
        self._start_error = None
        ready = asyncio.Event()
        self._serve_task = asyncio.create_task(self._serve(ready))
        await ready.wait()

        if self._start_error is not None:
            error, self._start_error = self._start_error, None
            self._serve_task = None
            raise error
        return list(self._tools.values())

    async def _serve(self, ready: asyncio.Event) -> None:
        """Owns ``stdio_client``/``ClientSession`` for this handle's whole
        running lifetime, in this one task — see the class docstring for
        why. Sets ``ready`` exactly once, whether startup succeeded or
        failed, so ``start()`` never hangs waiting for it."""
        stderr = StderrCapture()
        try:
            params = StdioServerParameters(
                command=self.config.command, args=self.config.args, env=self.config.env or None
            )
            async with (
                stdio_client(params, errlog=stderr) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools_result = await session.list_tools()

                self._session = session
                self._tools = {t.name: t for t in tools_result.tools}
                self.stderr = stderr
                ready.set()

                assert self._stop_event is not None
                await self._stop_event.wait()
        except Exception as exc:
            tail = "\n".join(stderr.tail())
            detail = f" — stderr: {tail}" if tail else ""
            self._start_error = McpServerError(
                f"mcp server {self.config.name!r} failed to start: {exc}{detail}"
            )
            ready.set()  # unblock a waiting start() even on failure
        finally:
            self._session = None
            self._tools = {}
            stderr.close()
            self.stderr = None

    async def stop(self) -> None:
        """Signal the serving task to tear down and wait for it. Safe to
        call even if the handle was never started, or already stopped."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._serve_task is not None:
            with contextlib.suppress(Exception):  # teardown errors are diagnostics, not this call's problem
                await self._serve_task
            self._serve_task = None
        self._stop_event = None

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
                               error_message=f"timed out after {self.call_timeout_seconds}s", arguments=arguments)
            raise McpServerError(
                f"mcp server {self.config.name!r} tool {tool_name!r} timed out "
                f"after {self.call_timeout_seconds}s"
            ) from exc
        except Exception as exc:
            self._record_call(
                tool_name, time.monotonic() - start, is_error=True, error_message=str(exc), arguments=arguments
            )
            raise McpServerError(
                f"mcp server {self.config.name!r} tool {tool_name!r} call failed: {exc}"
            ) from exc

        content_types = [getattr(block, "type", "?") for block in result.content]
        payload_bytes = sum(_block_payload_size(block) for block in result.content)
        content_preview = [_block_preview(block) for block in result.content]
        if result.structuredContent is not None:
            content_preview.append({"type": "structuredContent", "json": result.structuredContent})
        self._record_call(
            tool_name,
            time.monotonic() - start,
            is_error=bool(result.isError),
            content_types=content_types,
            payload_bytes=payload_bytes,
            arguments=arguments,
            content_preview=content_preview,
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
        arguments: dict[str, Any] | None = None,
        content_preview: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls.append(
            ToolCallRecord(
                tool_name=tool_name,
                duration_seconds=duration_seconds,
                is_error=is_error,
                content_types=content_types or [],
                payload_bytes=payload_bytes,
                error_message=error_message,
                arguments=arguments or {},
                content_preview=content_preview or [],
            )
        )


__all__ = ["DEFAULT_CALL_TIMEOUT_SECONDS", "McpServerError", "McpServerHandle", "StderrCapture", "ToolCallRecord"]
