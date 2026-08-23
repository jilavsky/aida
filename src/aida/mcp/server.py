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
import itertools
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from aida.config.secrets import get_secret
from aida.config.settings import McpServerConfig

DEFAULT_CALL_TIMEOUT_SECONDS = 60.0

#: B6: the one remaining hole in "secrets never touch YAML/JSON" — a real
#: MCP server config (e.g. one exported from Claude Desktop, or hand-
#: written for a private instrument API) routinely needs an API key/token
#: in its `env` block, and until now that meant a plaintext value sitting
#: right in `mcp.json`. Either prefix on an `env` value defers it to
#: aida.config.secrets instead — "keyring:" reads more naturally
#: standalone, "secret:" mirrors provider profiles' own `secret_ref`
#: terminology, so both are accepted.
_SECRET_ENV_PREFIXES = ("keyring:", "secret:")


def resolve_env_secrets(env: dict[str, str]) -> dict[str, str]:
    """Resolve any ``keyring:NAME``/``secret:NAME`` values in an MCP
    server's ``env`` block into real values via
    ``aida.config.secrets.get_secret`` — plain values pass through
    unchanged. Raises ``McpServerError`` (not e.g. KeyError) for a
    reference with nothing stored under that name, so a missing secret
    fails the same way any other bad server config does: isolated to that
    one server, with a clear message, rather than launching a subprocess
    that's missing a credential it needs and failing confusingly later."""
    resolved: dict[str, str] = {}
    for key, value in env.items():
        prefix = next((p for p in _SECRET_ENV_PREFIXES if value.startswith(p)), None)
        if prefix is None:
            resolved[key] = value
            continue
        secret_name = value[len(prefix) :]
        secret_value = get_secret(secret_name) if secret_name else None
        if secret_value is None:
            raise McpServerError(
                f"env var {key!r} references secret {secret_name!r} ({value!r}), but nothing is "
                f"stored under that name in the OS keychain (or AIDA_SECRET_{secret_name.upper()})"
            )
        resolved[key] = secret_value
    return resolved


def _scratch_env_defaults(cwd: Path | None) -> dict[str, str]:
    """``TMPDIR``/``TEMP``/``TMP`` pointed at ``cwd``, or ``{}`` if unset.

    Bug report: "Agents seem to be saving temporary files ... in random
    places" — traced to two independent causes: this server's subprocess
    previously had no ``cwd`` at all (inherited AIDA's own, wherever the
    user happened to launch it from), and separately, the ``mcp`` SDK's
    ``get_default_environment()`` never inherits ``TMPDIR``/``TEMP``/``TMP``
    from AIDA's own process regardless of what AIDA has set — so a tool
    that does the OS-default thing (``tempfile.mkdtemp()``, ``os.getcwd()``)
    had no way to land anywhere predictable. Setting all three covers every
    platform (``TMPDIR`` POSIX, ``TEMP``/``TMP`` Windows) without needing to
    know which one a given tool actually reads.
    """
    if cwd is None:
        return {}
    value = str(cwd)
    return {"TMPDIR": value, "TEMP": value, "TMP": value}


#: How long ``start()`` waits for a server to launch, answer the MCP
#: ``initialize`` handshake, and list its tools.
#:
#: A subprocess that starts successfully and then simply goes quiet — a bad
#: env var, a hung import, anything that reads stdin — used to block
#: ``start()`` forever: ``ClientSession`` was constructed with no
#: ``read_timeout_seconds``, so ``session.initialize()`` had no deadline of
#: its own, and ``start()`` did a bare ``await ready.wait()``. Nothing
#: recovered from that: ``start_all()`` never returned, so the CLI never
#: reached its prompt and the GUI sat on "Starting session…" indefinitely —
#: and ``stop()`` hung too, because ``_serve`` never reached its stop-event
#: wait, so setting that event did nothing. Every wait in this class is
#: bounded now.
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0

#: How long ``stop()`` waits for the serving task to unwind before
#: cancelling it, and how long it then waits for that cancellation.
DEFAULT_STOP_TIMEOUT_SECONDS = 10.0

_STDERR_TAIL_LINES = 200

#: Per-process, strictly increasing — see ToolCallRecord.seq's docstring
#: for why this exists instead of sorting by a clock reading.
_next_call_seq = itertools.count().__next__


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
    #: Monotonically increasing per-process sequence number, assigned at
    #: record-construction time. ``McpManager.recent_calls`` sorts by this
    #: (not ``recorded_at``) to merge several servers' call logs into one
    #: correctly ordered list. A clock reading is *not* reliable for this:
    #: real Windows CI flake — two real tool calls a few lines apart in a
    #: fast-running test landed within the same tick of Python 3.11's
    #: coarser ``time.monotonic()`` resolution on Windows (fixed in 3.13),
    #: compared equal, and a stable sort then preserved insertion order
    #: instead of the intended chronological order.
    seq: int = field(default_factory=_next_call_seq)
    #: ``time.monotonic()`` at record time — informational only (e.g. a
    #: future "N seconds ago" display); ordering uses ``seq`` instead, see
    #: its docstring above.
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
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
        cwd: Path | None = None,
    ) -> None:
        self.config = config
        self.call_timeout_seconds = call_timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        #: Working directory (and TMPDIR/TEMP/TMP source) for this server's
        #: subprocess — see ``_scratch_env_defaults``. ``None`` preserves the
        #: old behavior (inherit AIDA's own cwd/env) for any caller that
        #: doesn't pass one, e.g. direct unit tests of this class.
        self._cwd = cwd
        self.stderr: StderrCapture | None = None
        self.calls: list[ToolCallRecord] = []
        #: The server's own ``instructions`` string from the MCP
        #: ``initialize`` handshake (``mcp.types.InitializeResult.
        #: instructions``), or ``None`` if it didn't declare one. A FastMCP
        #: server author can write this specifically to teach an LLM how to
        #: use *that* server's tools (pyirena-mcp ships a detailed one) —
        #: AIDA used to call ``session.initialize()`` and discard the
        #: result entirely, so this was never seen by the model even though
        #: the server had already provided it. See
        #: ``aida.mcp.manager.McpManager.server_instructions``.
        self.instructions: str | None = None
        self._session: ClientSession | None = None
        self._tools: dict[str, Tool] = {}
        self._resolved_env: dict[str, str] = {}
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
        failure (bad command, crash before handshake, a server that never
        answers ``initialize`` within ``startup_timeout_seconds``); the
        caller isolates this to one server."""
        if self._session is not None:
            return list(self._tools.values())

        # B6: resolved here, synchronously, before the subprocess is even
        # spawned — a missing/misspelled secret reference fails immediately
        # with a clear message rather than launching a process that's
        # quietly missing a credential it needs. Scratch TMPDIR/TEMP/TMP
        # defaults go first so the server config's own `env` (if it sets
        # any of these explicitly) always wins.
        self._resolved_env = {**_scratch_env_defaults(self._cwd), **resolve_env_secrets(self.config.env)}

        self._stop_event = asyncio.Event()
        self._start_error = None
        ready = asyncio.Event()
        self._serve_task = asyncio.create_task(self._serve(ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=self.startup_timeout_seconds)
        except TimeoutError as exc:
            # Read the stderr tail *before* tearing the serving task down —
            # its `finally` closes the capture file.
            tail = "\n".join(self.stderr.tail()) if self.stderr is not None else ""
            detail = f" — stderr: {tail}" if tail else ""
            await self._abandon_serve_task()
            raise McpServerError(
                f"mcp server {self.config.name!r} did not finish starting within "
                f"{self.startup_timeout_seconds}s (no reply to the MCP initialize "
                f"handshake){detail}"
            ) from exc

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
        # Published before the handshake, not after it, so a start() that
        # times out waiting for `ready` can still report whatever the
        # subprocess printed on its way to going quiet.
        self.stderr = stderr
        try:
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self._resolved_env or None,
                cwd=self._cwd,
            )
            async with (
                stdio_client(params, errlog=stderr) as (read_stream, write_stream),
                # Backstop deadline on *every* request this session makes,
                # initialize/list_tools below included: without it, a server
                # that accepts a request and never replies leaves the await
                # pending forever. The explicit asyncio.wait_for()s in
                # start()/call_tool() are the primary, tighter bounds (and
                # give the clearer error messages); this one is deliberately
                # a little looser so those win the race in the normal case.
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self.call_timeout_seconds + 5),
                ) as session,
            ):
                init_result = await session.initialize()
                tools_result = await session.list_tools()

                self._session = session
                self._tools = {t.name: t for t in tools_result.tools}
                self.instructions = init_result.instructions
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
            self.instructions = None
            stderr.close()
            self.stderr = None

    async def stop(self) -> None:
        """Signal the serving task to tear down and wait for it, bounded by
        ``stop_timeout_seconds``. Safe to call even if the handle was never
        started, or already stopped.

        The wait has to be bounded because the stop event only ever reaches
        a server that got far enough to wait on it: a handle wedged
        mid-handshake never does, so setting the event achieves nothing and
        the old unbounded ``await self._serve_task`` hung here too. A task
        that ignores both the event and the cancellation that follows is
        abandoned — a leaked subprocess is a much smaller problem than an
        application that cannot shut down."""
        if self._stop_event is not None:
            self._stop_event.set()
        task, self._serve_task = self._serve_task, None
        self._stop_event = None
        if task is None:
            return
        done, _pending = await asyncio.wait({task}, timeout=self.stop_timeout_seconds)
        if not done:
            await self._abandon_serve_task(task)

    async def _abandon_serve_task(self, task: asyncio.Task[None] | None = None) -> None:
        """Cancel the serving task and give it a bounded chance to unwind.

        ``asyncio.wait`` rather than ``await task`` on purpose: it neither
        re-raises the task's ``CancelledError`` into this caller nor lets a
        teardown error escape (both are diagnostics, not this call's
        problem), and it returns at the timeout instead of hanging if the
        task refuses to unwind at all."""
        if task is None:
            task, self._serve_task = self._serve_task, None
        self._stop_event = None
        if task is None:
            return
        task.cancel()
        await asyncio.wait({task}, timeout=self.stop_timeout_seconds)

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


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "DEFAULT_STARTUP_TIMEOUT_SECONDS",
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "McpServerError",
    "McpServerHandle",
    "StderrCapture",
    "ToolCallRecord",
]
