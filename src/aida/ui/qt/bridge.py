"""Event-stream -> Qt bridge (PLAN.md Phase 5): drives a
``aida.cli.chat.ChatSession`` (an async API) from Qt's synchronous,
single-threaded GUI event loop, re-emitting every
``aida.core.events.AgentEvent`` as a Qt signal.

Design: a dedicated background thread runs its own asyncio event loop for
the lifetime of the app (``_AsyncLoopThread``); ``ChatBridge`` (a
``QObject`` that lives on the *Qt* thread, not the asyncio one) schedules
coroutines onto that loop via ``asyncio.run_coroutine_threadsafe`` and
re-emits results as signals. Qt signal emission is thread-safe *by
construction* here: emitting a signal from the background thread onto a
receiver object that lives on the Qt thread is automatically delivered via
a queued connection — no manual locking needed, and no widget code ever
touches asyncio directly.

Nothing in ``aida.core``/``aida.cli`` knows this module exists — it only
imports ``aida.cli.chat``'s public API (``start_session``, the error
classes) and ``aida.core.events``, both already Qt-free. That is what keeps
the "core remains importable and testable without Qt" rule intact.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from typing import Any

from aida.cli.chat import (
    ChatSession,
    UnknownMcpServerError,
    UnknownProfileError,
    UnknownWorkspaceError,
    start_session,
)
from aida.config.settings import Settings
from aida.mcp.manager import McpManager
from aida.persistence.recorder import ConversationNotFoundError
from aida.ui.qt._qt import QObject, QThread, Signal
from aida.workspace.safety import ConfirmationRequest

_STARTUP_ERRORS = (UnknownProfileError, UnknownWorkspaceError, UnknownMcpServerError, ConversationNotFoundError)


class AsyncLoopThread(QThread):
    """Owns one asyncio event loop, running for as long as this thread is
    alive. One instance serves the whole GUI process — asyncio loops aren't
    meant to be created per-request."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def run(self) -> None:  # noqa: D102 - QThread override, not a public API
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def wait_until_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("asyncio loop thread did not start in time")

    def stop(self) -> None:
        """Ask the loop to stop and block until the thread has exited.
        Safe to call even if the loop was never started."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.wait()


class ChatBridge(QObject):
    """Owns one ``ChatSession`` and drives it on an ``AsyncLoopThread``,
    turning its async generator API into Qt signals a widget can connect to
    without ever awaiting anything itself.

    Every ``AgentEvent`` a turn produces is re-emitted individually via
    ``event_received`` (in order — ``ChatSession.send`` already streams
    them in order) so a chat panel can render exactly the same way
    ``aida.cli.chat.print_event`` does for the CLI, just via a signal
    instead of a synchronous loop.
    """

    session_ready = Signal()
    startup_failed = Signal(str)
    turn_started = Signal()
    event_received = Signal(object)  # AgentEvent
    turn_finished = Signal()
    turn_failed = Signal(str)
    profile_switched = Signal(str)
    profile_switch_failed = Signal(str)
    # Phase 6: emitted from the background asyncio thread whenever a
    # SafetyGuard-gated tool needs a yes/no answer. The second argument is a
    # plain concurrent.futures.Future the receiver (MainWindow, on the Qt
    # thread) must resolve with a bool by calling future.set_result(...) —
    # see ChatBridge._confirm's docstring for why a plain Future (not a Qt
    # signal-based reply) is what bridges the two threads here.
    confirmation_requested = Signal(object, object)  # (ConfirmationRequest, concurrent.futures.Future[bool])

    def __init__(self, loop_thread: AsyncLoopThread, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop_thread = loop_thread
        self.session: ChatSession | None = None
        self.mcp_manager: McpManager | None = None
        # Startup is asynchronous, so a bridge can be asked to shut down
        # while its session is still being built (the user switches
        # workspace, or closes the window, before MCP servers finish
        # launching). These two track that window: `_start_future` lets
        # shutdown wait for the in-flight start instead of walking away
        # from it, and `_closing` tells `_start` not to announce a session
        # nobody wants any more. Without them, `shutdown()` returned
        # immediately (session was still None), the start then completed
        # unowned — leaking its MCP subprocesses and SQLite connection for
        # the rest of the process's life — and emitted `session_ready` into
        # a window that had already moved on to a different bridge.
        self._start_future: concurrent.futures.Future | None = None
        self._closing = False
        self._closed = False

    # --- startup -----------------------------------------------------------

    def start(self, settings: Settings, **start_session_kwargs: Any) -> None:
        """Kick off ``start_session(settings, **start_session_kwargs)`` on
        the background loop. Fires ``session_ready`` on success or
        ``startup_failed`` (with a human-readable message) on any of the
        known startup errors — never lets one reach the Qt thread as a
        raised exception.

        Unless the caller already supplied one, ``confirm_callback`` is
        defaulted to ``self._confirm`` — a real modal dialog on the Qt
        thread, per PLAN.md's "GUI: confirmation flow uses a real dialog"
        requirement (the CLI's own default, ``aida.cli.chat.cli_confirm``,
        would just block invisibly on stdin here since the GUI has no
        terminal)."""
        start_session_kwargs.setdefault("confirm_callback", self._confirm)
        self._start_future = asyncio.run_coroutine_threadsafe(
            self._start(settings, start_session_kwargs), self._loop_thread.loop
        )

    async def _confirm(self, request: ConfirmationRequest) -> bool:
        """The GUI's ``ConfirmCallback`` (Phase 6). Runs on the background
        asyncio thread (it's called from inside a tool coroutine that
        ``SafetyGuard`` is awaiting) but the actual yes/no decision has to
        come from a real modal dialog on the Qt thread. Bridges the two via
        a plain ``concurrent.futures.Future`` (thread-safe by design, unlike
        an ``asyncio.Future``): emitting ``confirmation_requested`` onto a
        Qt-thread-owned receiver is automatically a thread-safe queued
        delivery (same mechanism ``event_received`` already relies on — see
        this module's docstring), and ``asyncio.wrap_future`` lets this
        coroutine ``await`` the plain ``Future`` the Qt-side handler
        resolves once the user answers the dialog."""
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.confirmation_requested.emit(request, future)
        return await asyncio.wrap_future(future)

    async def _start(self, settings: Settings, kwargs: dict[str, Any]) -> None:
        try:
            session, mcp_manager = await start_session(settings, **kwargs)
        except _STARTUP_ERRORS as exc:
            if not self._closing:
                self.startup_failed.emit(str(exc))
            return
        self.session = session
        self.mcp_manager = mcp_manager
        if self._closing:
            # Shut down while we were starting: hand the finished session
            # straight to the teardown path (which is awaiting this
            # coroutine) instead of announcing it. Cleanup itself stays in
            # `_shutdown` so there is exactly one close path.
            return
        self.session_ready.emit()

    # --- turns ---------------------------------------------------------------

    def send(self, user_text: str) -> None:
        """Start a new turn. No-op if the session hasn't finished starting
        yet — callers (the input box) should be disabled until
        ``session_ready``."""
        if self.session is None:
            return
        asyncio.run_coroutine_threadsafe(self._drain(user_text), self._loop_thread.loop)

    async def _drain(self, user_text: str) -> None:
        self.turn_started.emit()
        try:
            async for event in self.session.send(user_text):
                self.event_received.emit(event)
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            self.turn_failed.emit(str(exc))
        finally:
            self.turn_finished.emit()

    def cancel(self) -> None:
        """Request cancellation of the in-flight turn. ``ChatSession.cancel``
        is a plain synchronous flag-set (see its docstring) — safe to call
        directly from the Qt thread, no scheduling onto the loop needed."""
        if self.session is not None:
            self.session.cancel()

    # --- profile switching -----------------------------------------------

    def switch_profile(self, name: str) -> None:
        if self.session is None:
            return
        asyncio.run_coroutine_threadsafe(self._switch_profile(name), self._loop_thread.loop)

    async def _switch_profile(self, name: str) -> None:
        try:
            await self.session.switch_profile(name)
        except UnknownProfileError as exc:
            self.profile_switch_failed.emit(str(exc))
            return
        self.profile_switched.emit(name)

    # --- shutdown ------------------------------------------------------------

    def shutdown(self, timeout: float = 5.0) -> None:
        """Close the session's provider/recorder/MCP connections on the
        background loop and block until done (or ``timeout``) — call this
        before the app quits or before replacing this bridge, from the Qt
        thread. Swallows errors: a failed cleanup must never block the app
        from closing.

        Deliberately *not* short-circuited on ``self.session is None``: a
        session still being built is exactly the case that used to leak
        (see ``__init__``). ``_shutdown`` waits for any in-flight start and
        then closes whatever it produced."""
        self._closing = True
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop_thread.loop)
        with contextlib.suppress(Exception):  # best-effort cleanup on the way out
            future.result(timeout=timeout)

    async def _shutdown(self) -> None:
        if self._start_future is not None:
            # Runs on the same loop the start was scheduled on, so awaiting
            # it here just yields until it finishes; errors were already
            # handled inside _start.
            with contextlib.suppress(Exception):
                await asyncio.wrap_future(self._start_future)
            self._start_future = None
        if self._closed:
            return  # idempotent: repeated shutdown() calls must not double-close
        self._closed = True
        if self.session is not None:
            await self.session.aclose()
        if self.mcp_manager is not None:
            await self.mcp_manager.aclose()


__all__ = ["AsyncLoopThread", "ChatBridge"]
