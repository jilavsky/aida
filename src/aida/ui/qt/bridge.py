"""Event-stream -> Qt bridge: drives an async ``aida.core.session.ChatSession``
from Qt's single-threaded GUI loop, re-emitting every ``AgentEvent`` as a Qt
signal.

Design: ``AsyncLoopThread`` runs one asyncio loop for the app's lifetime;
``ChatBridge`` (a ``QObject`` on the *Qt* thread) schedules coroutines onto it
via ``asyncio.run_coroutine_threadsafe``. Signals emitted from the loop thread
to Qt-thread receivers are delivered as queued connections, so no locking is
needed and widget code never touches asyncio.

Layering: this module imports only ``aida.core`` public API (never
``aida.cli``), and nothing in ``aida.core`` imports it — core must stay
importable and testable without Qt.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from pathlib import Path
from typing import Any

from aida.coding.runner import run_python_script, terminate_run
from aida.config.logging_setup import get_logger
from aida.config.paths import ensure_scratch_dir, knowledge_db_path
from aida.config.settings import (
    EmbeddingProfile,
    KnowledgeBaseConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
)
from aida.core.confirmation import ConfirmAnswer, ConfirmationRequest, RememberingConfirm
from aida.core.session import (
    ChatSession,
    NoWorkspaceFoldersError,
    UnknownMcpServerError,
    UnknownProfileError,
    UnknownWorkspaceError,
    start_session,
)
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import IngestResult
from aida.knowledge.rag.ingest import rebuild as ingest_rebuild
from aida.knowledge.rag.ingest import update as ingest_update
from aida.mcp.manager import NAMESPACE_SEPARATOR, McpManager
from aida.mcp.server import McpServerError
from aida.persistence.recorder import ConversationNotFoundError
from aida.providers.base import ImageRef
from aida.providers.profiles import (
    UnknownProviderKindError,
    build_embeddings_provider,
    validate_embedding_profile,
    validate_profile,
)
from aida.ui.qt._qt import QObject, QThread, Signal

logger = get_logger("ui.bridge")

_STARTUP_ERRORS = (
    UnknownProfileError,
    UnknownWorkspaceError,
    UnknownMcpServerError,
    ConversationNotFoundError,
)


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
            # Cancel pending tasks and let them unwind before close(); closing
            # with tasks mid-await leaves them to the GC, which reports
            # "coroutine ignored GeneratorExit" at some unrelated later point.
            # The unwind run must stay bounded: a task that ignores
            # cancellation would otherwise hang shutdown forever.
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                with contextlib.suppress(TimeoutError):
                    self.loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True), timeout=2.0
                        )
                    )
            self.loop.close()

    def wait_until_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("asyncio loop thread did not start in time")

    #: Bounds ``wait()`` in ``stop()``. On Windows, ``ProactorEventLoop.close()``
    #: waits with no timeout for every in-flight overlapped I/O; a subprocess
    #: pipe whose process was force-killed (``taskkill /F``, see
    #: ``aida.coding.runner._terminate_tree``) may never complete, hanging
    #: ``close()`` forever. This is the outer backstop for that case (POSIX's
    #: SelectorEventLoop has no such wait).
    _STOP_JOIN_TIMEOUT_MS = 10_000

    def stop(self) -> None:
        """Ask the loop to stop and block until the thread has exited, or
        until ``_STOP_JOIN_TIMEOUT_MS`` passes. Safe to call even if the
        loop was never started.

        Must never block forever: it runs right after ``app.exec()`` returns,
        so a hang would leave a seemingly closed app needing a force-kill
        (and one wedged thread would hang the test suite). On timeout the
        thread is abandoned unjoined; it does not prevent process exit."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if not self.wait(self._STOP_JOIN_TIMEOUT_MS):
            logger.warning(
                "AsyncLoopThread did not exit within %.1fs of stop() — abandoning it "
                "rather than blocking the caller forever (see _STOP_JOIN_TIMEOUT_MS)",
                self._STOP_JOIN_TIMEOUT_MS / 1000,
            )


class ChatBridge(QObject):
    """Owns one ``ChatSession`` and drives it on an ``AsyncLoopThread``,
    turning its async generator API into Qt signals a widget can connect to
    without ever awaiting anything itself.

    Every ``AgentEvent`` a turn produces is re-emitted, in order, via
    ``event_received``, so a chat panel renders the same stream the CLI's
    ``print_event`` does.

    Most background actions report back through one finished/failed signal
    pair; the receiving dialog or panel then re-renders from current state.
    """

    session_ready = Signal()
    startup_failed = Signal(str)
    turn_started = Signal()
    event_received = Signal(object)  # AgentEvent
    turn_finished = Signal()
    turn_failed = Signal(str)
    profile_switched = Signal(str)
    profile_switch_failed = Signal(str)
    # "Compact Conversation" failure/no-op only; success arrives via
    # event_received (see compact_context).
    compaction_failed = Signal(str)  # message: an error, or "nothing to compact yet"
    # Emitted from the loop thread when a SafetyGuard-gated tool needs an
    # answer. The receiver (Qt thread) must resolve the Future with a
    # ConfirmAnswer via set_result(); see _confirm_interactive.
    confirmation_requested = Signal(
        object, object
    )  # (ConfirmationRequest, concurrent.futures.Future[ConfirmAnswer])
    # MCP live control: one changed/failed pair for every verb
    # (start/stop/restart/register/unregister).
    mcp_server_status_changed = Signal(str)  # server name
    mcp_server_action_failed = Signal(str, str)  # (server name, error message)
    mcp_connection_tested = Signal(str, object)  # (server name, ConnectionTestResult)
    # Knowledge base build/update (embedding calls are network I/O).
    kb_ingest_finished = Signal(str, object)  # (kb name, IngestResult)
    kb_ingest_failed = Signal(str, str)  # (kb name, error message)
    # Profile "Test" button (real network pings). Separate signals per kind:
    # the dialog refreshes chat and embedding profile lists independently.
    profile_validated = Signal(str, object)  # (profile name, ProfileValidation)
    embedding_profile_validated = Signal(str, object)  # (profile name, ProfileValidation)
    # Code editor Run/Kill (a real subprocess).
    script_run_finished = Signal(object)  # RunResult
    script_run_failed = Signal(str)  # error message
    # "Export Conversation As…" for the active session. Must run on the loop
    # thread: the recorder's sqlite3 connection was created there, and sqlite3
    # raises ProgrammingError when another thread uses it.
    conversation_export_finished = Signal(str)  # path written to
    conversation_export_failed = Signal(str)  # error message
    # Workspace source/target folders applied to the live session
    # (ChatSession.update_workspace_folders).
    workspace_folders_applied = Signal(str)  # summary to show the user
    workspace_folders_apply_failed = Signal(str)  # error message
    # Global allowed folders from Settings applied to the live session
    # (ChatSession.update_global_allowed_folders). Separate from the workspace
    # pair because the status text differs: Settings already saved config.yaml.
    global_folders_applied = Signal(str)  # summary to show the user
    global_folders_apply_failed = Signal(str)  # error message
    # Skills toggled in the Skills panel, applied to the live session
    # (ChatSession.update_skills).
    skills_applied = Signal(str)  # summary to show the user
    skills_apply_failed = Signal(str)  # error message
    # Private: carries (public signal, args) from the loop thread to
    # _deliver_unless_closing on the Qt thread. See _emit_unless_closing.
    _relay = Signal(object, object)

    def __init__(self, loop_thread: AsyncLoopThread, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop_thread = loop_thread
        self._relay.connect(self._deliver_unless_closing)
        self.session: ChatSession | None = None
        self.mcp_manager: McpManager | None = None
        # Set in start(); lets an McpManager built later by
        # _ensure_mcp_manager() get the same scratch folder start_session wires in.
        self._scratch_dir: Path | None = None
        # The single RememberingConfirm shared by start() and
        # _ensure_mcp_manager(): separate instances would fragment the chat's
        # remembered approvals. Its lifetime is this bridge's (every New
        # Chat/resume/workspace switch builds a new bridge), which is what
        # scopes "Allow for this chat".
        self._remembering_confirm: RememberingConfirm | None = None
        # Shutdown can arrive while the session is still being built.
        # shutdown() awaits `_start_future` so the finished session is closed
        # rather than leaked (MCP subprocesses, SQLite), and `_closing` stops
        # `_start` from emitting session_ready into a window that moved on.
        self._start_future: concurrent.futures.Future | None = None
        # The in-flight turn; shutdown() must finish it before closing the
        # session, so a retired bridge neither streams into the new chat nor
        # has its session closed under a running turn.
        self._turn_future: concurrent.futures.Future | None = None
        # Subprocess of the script run_script is running, for
        # cancel_script_run; None when nothing is running.
        self._running_script_proc: asyncio.subprocess.Process | None = None
        self._closing = False
        self._closed = False

    # --- startup -----------------------------------------------------------

    def start(self, settings: Settings, **start_session_kwargs: Any) -> None:
        """Kick off ``start_session(settings, **start_session_kwargs)`` on
        the background loop. Fires ``session_ready`` on success or
        ``startup_failed`` (with a human-readable message) on any of the
        known startup errors — never lets one reach the Qt thread as a
        raised exception.

        ``confirm_callback`` defaults to ``_remembering_confirm_callback()``,
        a modal dialog on the Qt thread that remembers "Allow for this chat"
        answers. The CLI default would block invisibly on stdin here."""
        start_session_kwargs.setdefault("confirm_callback", self._remembering_confirm_callback())
        self._scratch_dir = ensure_scratch_dir(settings.app.scratch_dir)
        self._start_future = asyncio.run_coroutine_threadsafe(
            self._start(settings, start_session_kwargs), self._loop_thread.loop
        )

    def _remembering_confirm_callback(self) -> RememberingConfirm:
        """The one ``RememberingConfirm`` this bridge hands out; it must be
        shared (see ``_remembering_confirm`` in ``__init__``)."""
        if self._remembering_confirm is None:
            self._remembering_confirm = RememberingConfirm(self._confirm_interactive)
        return self._remembering_confirm

    async def _confirm_interactive(self, request: ConfirmationRequest) -> ConfirmAnswer:
        """The GUI's raw, tri-state ``RawConfirmCallback``.

        Runs on the loop thread, but the answer comes from a modal dialog on
        the Qt thread. A ``concurrent.futures.Future`` (thread-safe, unlike an
        ``asyncio.Future``) crosses the threads: the queued
        ``confirmation_requested`` signal carries it to the Qt side, which
        resolves it, and ``asyncio.wrap_future`` awaits it here.

        Always wrapped by ``_remembering_confirm_callback()``, which maps the
        ``ConfirmAnswer`` to the bool ``ConfirmCallback`` consumers expect."""
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.confirmation_requested.emit(request, future)
        return await asyncio.wrap_future(future)

    async def _start(self, settings: Settings, kwargs: dict[str, Any]) -> None:
        try:
            # Startup notices are shown in the chat panel (session.startup_notices).
            session, mcp_manager = await start_session(settings, echo_notices=False, **kwargs)
        except _STARTUP_ERRORS as exc:
            self._emit_unless_closing(self.startup_failed, str(exc))
            return
        self.session = session
        self.mcp_manager = mcp_manager
        # Post-turn events (e.g. ConversationTitled) arrive via this hook.
        session.on_background_event = self._emit_background_event
        # Not announced once closing; _shutdown (awaiting us) is the one close path.
        self._emit_unless_closing(self.session_ready)

    def _emit_background_event(self, event: object) -> None:
        self._emit_unless_closing(self.event_received, event)

    def _emit_unless_closing(self, signal: Any, *args: Any) -> None:
        """Emit ``signal(*args)`` unless this bridge is retiring — checked
        both here and again on the Qt thread when the signal is delivered.

        A cross-thread emit is queued, and Qt still delivers a queued call
        after ``disconnect()``. So checking ``_closing`` only on the loop
        thread lets an event that was already queued when the user pressed
        New Chat render into the *new* chat panel. The private ``_relay``
        has this bridge as its receiver, so its queued call runs on the Qt
        thread, where ``shutdown()`` sets ``_closing`` before the window
        disconnects; and Qt drops calls queued for a bridge already deleted."""
        if not self._closing:
            self._relay.emit(signal, args)

    def _deliver_unless_closing(self, signal: Any, args: tuple) -> None:
        if not self._closing:
            signal.emit(*args)

    # --- turns ---------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """Whether a turn is in flight right now.

        Lets callers choose between starting a turn and queueing into the
        running one (``queue_user_message``).

        The ``.done()`` check is required: ``send()`` assigns
        ``_turn_future`` on the Qt thread after scheduling, while ``_drain``
        clears it on the loop thread. If ``_drain`` finishes first (e.g.
        ``SessionBusyError`` on the first ``__anext__``), the assignment
        lands after the clear and, without ``.done()``, would read as busy
        forever.
        """
        return self._turn_future is not None and not self._turn_future.done()

    def send(
        self,
        user_text: str,
        *,
        images: list[ImageRef] | None = None,
        attachment_paths: list[str] | None = None,
        attachment_texts: dict[str, str] | None = None,
    ) -> None:
        """Start a new turn. No-op if the session hasn't finished starting
        yet — callers (the input box) should be disabled until
        ``session_ready``.

        Args:
            user_text: The user's message.
            images: Image attachments, passed through to ``ChatSession.send``.
            attachment_paths: The attachments as paths, so the conversation
                keeps its own copies (``aida.documents.attachments``).
            attachment_texts: Text already extracted from those attachments,
                saved beside the copies (avoids re-parsing)."""
        if self.session is None or self._closing:
            return
        self._turn_future = asyncio.run_coroutine_threadsafe(
            self._drain(user_text, images, attachment_paths, attachment_texts),
            self._loop_thread.loop,
        )

    async def _drain(
        self,
        user_text: str,
        images: list[ImageRef] | None = None,
        attachment_paths: list[str] | None = None,
        attachment_texts: dict[str, str] | None = None,
    ) -> None:
        """Stream one turn's events out as signals.

        Every emit goes through ``_emit_unless_closing``: a retiring bridge
        must go quiet or stale events render into the new session's panel. The generator
        is still drained to completion, never abandoned, so
        ``ChatSession.send``'s ``finally`` cleanup and ``AgentLoop``'s
        answers to cancelled tool calls still run; ``shutdown`` sets the
        cancel flag first, so this ends promptly."""
        self._emit_unless_closing(self.turn_started)
        try:
            async for event in self.session.send(
                user_text,
                images=images,
                attachment_paths=attachment_paths,
                attachment_texts=attachment_texts,
            ):
                self._emit_unless_closing(self.event_received, event)
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            self._emit_unless_closing(self.turn_failed, str(exc))
        finally:
            self._turn_future = None
            self._emit_unless_closing(self.turn_finished)

    def cancel(self) -> None:
        """Request cancellation of the in-flight turn. ``ChatSession.cancel``
        only sets a flag, so it is safe to call from the Qt thread."""
        if self.session is not None:
            self.session.cancel()

    def queue_user_message(self, text: str) -> bool:
        """Hand the in-flight turn something the user typed while it was
        running; ``True`` if it was queued.

        Called directly from the Qt thread: ``AgentLoop``'s queue is a deque
        touched only by append/popleft. ``False`` means no turn is in flight;
        send the text as an ordinary turn instead.
        """
        if self.session is None or not self.is_busy:
            return False
        self.session.queue_user_message(text)
        return True

    def take_undelivered_messages(self) -> list[str]:
        """Queued text the finished turn never got to — see
        ``AgentLoop.take_undelivered_messages``. Empty when there is no
        session."""
        if self.session is None:
            return []
        return self.session.take_undelivered_messages()

    # --- profile switching -----------------------------------------------

    def switch_profile(self, name: str) -> None:
        """Switch the session's provider profile.

        Refused while a turn is in flight, because the switch closes the
        provider the running ``AgentLoop`` streams from. This backs up
        ``MainWindow`` disabling the selector during turns."""
        if self.session is None:
            return
        if self.is_busy:
            self.profile_switch_failed.emit(
                "Can't switch profile while a turn is running — stop it or wait for it to finish."
            )
            return
        asyncio.run_coroutine_threadsafe(self._switch_profile(name), self._loop_thread.loop)

    async def _switch_profile(self, name: str) -> None:
        try:
            await self.session.switch_profile(name)
        except Exception as exc:  # noqa: BLE001 - see below
            # Deliberately broad: bad `kind:`, missing secret, SDK rejecting a
            # base_url, SessionBusyError from a racing turn... Anything that
            # escapes is lost in an unawaited future and the UI never hears
            # back. switch_profile is atomic, so on failure the session is
            # untouched, which is what the handler tells the user.
            logger.exception("switching to profile %r failed", name)
            self.profile_switch_failed.emit(str(exc))
            return
        self.profile_switched.emit(name)

    # --- manual compaction ----------------------------------------------------

    def compact_context(self) -> None:
        """ "Compact Conversation" menu action (the CLI's ``/compact``):
        summarize all but the most recent turns now, regardless of budget.

        Success is emitted on ``event_received``: the ``ContextTrimmed`` is
        the same event automatic compaction produces, so ``MainWindow``
        handles both in one place. Only no-op/failure outcomes, which have
        no ``AgentEvent``, use ``compaction_failed``."""
        if self.session is None:
            return
        asyncio.run_coroutine_threadsafe(self._compact_context(), self._loop_thread.loop)

    async def _compact_context(self) -> None:
        try:
            event = await self.session.compact_now()
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            self.compaction_failed.emit(str(exc))
            return
        if event is None:
            self.compaction_failed.emit("Nothing to compact yet — not enough history.")
            return
        self.event_received.emit(event)

    # --- live workspace folders -------------------------------------------

    def update_workspace_folders(
        self, source_folders: list[str], target_folder: str | None
    ) -> None:
        """Apply new source/target folders to the running session, so they
        are readable and announced to the model now, not in the next chat.

        Marshalled onto the loop (though the call is synchronous) because it
        rewrites ``session.messages[0]`` and the guard's allowed roots, which
        a running turn reads. Deliberately allowed mid-turn, unlike
        ``switch_profile``: fixing folders while the agent runs is the main
        use case."""
        if self.session is None:
            self.workspace_folders_apply_failed.emit(
                "No chat is running yet — folders will apply when one starts."
            )
            return
        asyncio.run_coroutine_threadsafe(
            self._update_workspace_folders(list(source_folders), target_folder),
            self._loop_thread.loop,
        )

    async def _update_workspace_folders(
        self, source_folders: list[str], target_folder: str | None
    ) -> None:
        if self.session is None:  # closed between the check above and here
            return
        try:
            summary = self.session.update_workspace_folders(
                source_folders=source_folders, target_folder=target_folder
            )
        except NoWorkspaceFoldersError as exc:
            self.workspace_folders_apply_failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            logger.exception("applying workspace folders to the live session failed")
            self.workspace_folders_apply_failed.emit(str(exc))
            return
        self.workspace_folders_applied.emit(summary)

    def update_global_allowed_folders(self, allowed_folders: list[str]) -> None:
        """Apply Settings' new global allowed-folders list to the running
        session, on the loop for the same reason as
        ``update_workspace_folders``. No-op with no chat running: the next
        chat reads it from settings."""
        if self.session is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._update_global_allowed_folders(list(allowed_folders)), self._loop_thread.loop
        )

    async def _update_global_allowed_folders(self, allowed_folders: list[str]) -> None:
        if self.session is None:  # closed between the check above and here
            return
        try:
            summary = self.session.update_global_allowed_folders(allowed_folders)
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            logger.exception("applying global allowed folders to the live session failed")
            self.global_folders_apply_failed.emit(str(exc))
            return
        if summary:
            self.global_folders_applied.emit(summary)

    # --- live skills ---------------------------------------------------------

    def update_skills(self, skill_names: list[str]) -> None:
        """Apply a new set of skills to the running session, on the loop
        because it rewrites ``session.messages[0]``, which a running turn reads."""
        if self.session is None:
            self.skills_apply_failed.emit("No chat is running yet.")
            return
        asyncio.run_coroutine_threadsafe(
            self._update_skills(list(skill_names)), self._loop_thread.loop
        )

    async def _update_skills(self, skill_names: list[str]) -> None:
        if self.session is None:  # closed between the check above and here
            return
        try:
            summary = self.session.update_skills(skill_names)
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            logger.exception("applying skills to the live session failed")
            self.skills_apply_failed.emit(str(exc))
            return
        self.skills_applied.emit(summary)

    # --- MCP server live control ------------------------------------------------

    def _ensure_mcp_manager(self) -> McpManager:
        """Return the session's ``McpManager``, creating it on first use.

        A session with no MCP servers configured never builds one, so this
        is what lets "Add Server" + "Start" work in such a session."""
        if self.mcp_manager is None:
            # Must use the shared confirm callback; McpManager's deny_all
            # default would silently refuse every confirm-flagged tool.
            self.mcp_manager = McpManager(
                [],
                confirm_callback=self._remembering_confirm_callback(),
                scratch_dir=self._scratch_dir or ensure_scratch_dir(),
            )
        return self.mcp_manager

    def _drop_server_tools_from_session(self, name: str) -> None:
        """Remove every namespaced tool belonging to ``name`` from the live
        session's tool dict. ``ChatSession.tools`` is the same dict object as
        ``AgentLoop.tools``, so the running loop sees the change at once."""
        if self.session is None:
            return
        prefix = f"{name}{NAMESPACE_SEPARATOR}"
        for key in [k for k in self.session.tools if k.startswith(prefix)]:
            del self.session.tools[key]

    def register_mcp_server(self, config: McpServerConfig) -> None:
        """Register a new or edited server config with the live manager,
        without starting it, so a later "Start" works without a new chat."""
        asyncio.run_coroutine_threadsafe(self._register_mcp_server(config), self._loop_thread.loop)

    async def _register_mcp_server(self, config: McpServerConfig) -> None:
        self._ensure_mcp_manager().add_server_config(config)
        self.mcp_server_status_changed.emit(config.name)

    def unregister_mcp_server(self, name: str) -> None:
        asyncio.run_coroutine_threadsafe(self._unregister_mcp_server(name), self._loop_thread.loop)

    async def _unregister_mcp_server(self, name: str) -> None:
        if self.mcp_manager is not None:
            await self.mcp_manager.remove_server_config(name)
        self._drop_server_tools_from_session(name)
        self.mcp_server_status_changed.emit(name)

    def start_mcp_server(self, name: str) -> None:
        asyncio.run_coroutine_threadsafe(self._start_mcp_server(name), self._loop_thread.loop)

    async def _start_mcp_server(self, name: str) -> None:
        try:
            new_tools = await self._ensure_mcp_manager().start_server(name)
        except McpServerError as exc:
            self.mcp_server_action_failed.emit(name, str(exc))
            return
        if self.session is not None:
            self.session.tools.update(new_tools)
        self.mcp_server_status_changed.emit(name)

    def stop_mcp_server(self, name: str) -> None:
        asyncio.run_coroutine_threadsafe(self._stop_mcp_server(name), self._loop_thread.loop)

    async def _stop_mcp_server(self, name: str) -> None:
        if self.mcp_manager is not None:
            await self.mcp_manager.stop_server(name)
        self._drop_server_tools_from_session(name)
        self.mcp_server_status_changed.emit(name)

    def restart_mcp_server(self, name: str) -> None:
        asyncio.run_coroutine_threadsafe(self._restart_mcp_server(name), self._loop_thread.loop)

    async def _restart_mcp_server(self, name: str) -> None:
        self._drop_server_tools_from_session(name)  # its tool list may change on restart
        try:
            new_tools = await self._ensure_mcp_manager().restart_server(name)
        except McpServerError as exc:
            self.mcp_server_action_failed.emit(name, str(exc))
            return
        if self.session is not None:
            self.session.tools.update(new_tools)
        self.mcp_server_status_changed.emit(name)

    def test_mcp_connection(self, config: McpServerConfig) -> None:
        asyncio.run_coroutine_threadsafe(self._test_mcp_connection(config), self._loop_thread.loop)

    async def _test_mcp_connection(self, config: McpServerConfig) -> None:
        result = await self._ensure_mcp_manager().test_connection(config)
        self.mcp_connection_tested.emit(config.name, result)

    # --- provider/embedding profile validation ----------------------------------

    def validate_provider_profile(self, profile: ProviderProfile) -> None:
        """ "Test" button: ping the profile's real endpoint (see
        ``validate_profile``) on the background loop."""
        asyncio.run_coroutine_threadsafe(
            self._validate_provider_profile(profile), self._loop_thread.loop
        )

    async def _validate_provider_profile(self, profile: ProviderProfile) -> None:
        result = await validate_profile(profile)
        self.profile_validated.emit(profile.name, result)

    def validate_embedding_provider_profile(self, profile: EmbeddingProfile) -> None:
        """Same as ``validate_provider_profile``, for an ``EmbeddingProfile``."""
        asyncio.run_coroutine_threadsafe(
            self._validate_embedding_provider_profile(profile), self._loop_thread.loop
        )

    async def _validate_embedding_provider_profile(self, profile: EmbeddingProfile) -> None:
        result = await validate_embedding_profile(profile)
        self.embedding_profile_validated.emit(profile.name, result)

    # --- knowledge base build/update --------------------------------------------

    def rebuild_knowledge_base(
        self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile
    ) -> None:
        """Full re-ingest of ``kb``: every file re-chunked and re-embedded.
        Takes configs rather than names so the bridge holds no ``Settings``."""
        asyncio.run_coroutine_threadsafe(
            self._run_kb_ingest(kb, embedding_profile, rebuild=True), self._loop_thread.loop
        )

    def update_knowledge_base(
        self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile
    ) -> None:
        """Incremental re-ingest of ``kb``: only files changed since the
        last build/update are re-embedded."""
        asyncio.run_coroutine_threadsafe(
            self._run_kb_ingest(kb, embedding_profile, rebuild=False), self._loop_thread.loop
        )

    async def _run_kb_ingest(
        self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile, *, rebuild: bool
    ) -> None:
        try:
            embeddings_provider = build_embeddings_provider(embedding_profile)
        except UnknownProviderKindError as exc:
            self.kb_ingest_failed.emit(kb.name, str(exc))
            return

        conn = kb_index.connect(knowledge_db_path(kb.name))
        try:
            ingest_fn = ingest_rebuild if rebuild else ingest_update
            result: IngestResult = await ingest_fn(conn, kb, embeddings_provider)
        except Exception as exc:  # noqa: BLE001 - a bad individual file is already handled inside ingest;
            # provider-level failures (auth, network, base_url) must reach
            # the dialog as a message, not crash the loop.
            self.kb_ingest_failed.emit(kb.name, str(exc))
            return
        finally:
            conn.close()
            await embeddings_provider.aclose()

        self.kb_ingest_finished.emit(kb.name, result)

    # --- code editor run/kill ---------------------------------------------------

    def run_script(
        self, path: str, args: list[str], *, interpreter: str | None, cwd: str, timeout: float
    ) -> None:
        """Run ``path`` as a subprocess on the background loop; reports via
        ``script_run_finished``/``script_run_failed``."""
        asyncio.run_coroutine_threadsafe(
            self._run_script(path, args, interpreter=interpreter, cwd=cwd, timeout=timeout),
            self._loop_thread.loop,
        )

    def cancel_script_run(self) -> None:
        """Kill the script ``run_script`` is running, if any. Scheduled on
        the loop thread so the ``Process`` is only ever touched there."""
        asyncio.run_coroutine_threadsafe(self._cancel_running_script(), self._loop_thread.loop)

    async def _cancel_running_script(self) -> None:
        if self._running_script_proc is not None:
            # terminate_run, not proc.kill(): it kills the whole process group,
            # including anything the script spawned (pools, plot backends).
            await terminate_run(self._running_script_proc)

    async def _run_script(
        self, path: str, args: list[str], *, interpreter: str | None, cwd: str, timeout: float
    ) -> None:
        def _on_started(proc) -> None:
            self._running_script_proc = proc

        try:
            result = await run_python_script(
                path,
                args,
                interpreter=interpreter,
                cwd=cwd,
                timeout=timeout,
                on_started=_on_started,
            )
        except OSError as exc:
            self.script_run_failed.emit(str(exc))
            return
        finally:
            self._running_script_proc = None

        self.script_run_finished.emit(result)

    # --- export conversation -----------------------------------------------------

    def export_conversation(self, destination: str, tool_result_mode: str) -> None:
        """Snapshot the active conversation to ``destination``, on the loop
        thread that owns the recorder's sqlite3 connection (see the
        ``conversation_export_*`` signals)."""
        if self.session is None or self.session.recorder is None:
            self.conversation_export_failed.emit("No conversation open yet.")
            return
        asyncio.run_coroutine_threadsafe(
            self._export_conversation(destination, tool_result_mode), self._loop_thread.loop
        )

    async def _export_conversation(self, destination: str, tool_result_mode: str) -> None:
        # Re-checked: the session may be torn down before this runs.
        if self.session is None or self.session.recorder is None:
            self.conversation_export_failed.emit("No conversation open yet.")
            return
        try:
            path = self.session.recorder.export_transcript_to(
                Path(destination), tool_result_mode=tool_result_mode
            )
        except OSError as exc:
            self.conversation_export_failed.emit(str(exc))
            return
        self.conversation_export_finished.emit(str(path))

    # --- shutdown ------------------------------------------------------------

    def shutdown(self, timeout: float = 5.0) -> None:
        """Close the session's provider/recorder/MCP connections on the
        background loop and block until done (or ``timeout``) — call this
        before the app quits or before replacing this bridge, from the Qt
        thread. Swallows errors: a failed cleanup must never block the app
        from closing.

        Deliberately not skipped when ``session is None``: a session still
        being built must be awaited and closed, or it leaks (see
        ``__init__``)."""
        self._closing = True
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop_thread.loop)
        with contextlib.suppress(Exception):  # best-effort cleanup on the way out
            future.result(timeout=timeout)

    async def _shutdown(self) -> None:
        if self._start_future is not None:
            # Same loop, so this just yields until _start finishes (it
            # handles its own errors).
            with contextlib.suppress(Exception):
                await asyncio.wrap_future(self._start_future)
            self._start_future = None
        # Snapshot before awaiting: _drain clears the attribute in its own
        # `finally`, which can land between the check and the await.
        turn_future, self._turn_future = self._turn_future, None
        if turn_future is not None:
            # A running turn must finish before session.aclose() closes the
            # provider and recorder under it. cancel() is cooperative, so
            # the turn unwinds through its normal cleanup path.
            if self.session is not None:
                self.session.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wrap_future(turn_future)
        if self._closed:
            return  # idempotent: repeated shutdown() calls must not double-close
        self._closed = True
        if self.session is not None:
            await self.session.aclose()
        if self.mcp_manager is not None:
            await self.mcp_manager.aclose()


__all__ = ["AsyncLoopThread", "ChatBridge"]
