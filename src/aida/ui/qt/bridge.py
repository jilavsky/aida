"""Event-stream -> Qt bridge (PLAN.md Phase 5): drives a
``aida.core.session.ChatSession`` (an async API) from Qt's synchronous,
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

Nothing in ``aida.core`` knows this module exists — it only imports
``aida.core.session``'s public API (``start_session``, the error classes)
and ``aida.core.events``, both already Qt-free. That is what keeps the
"core remains importable and testable without Qt" rule intact. (B8: this
used to import from ``aida.cli.chat`` — the one place the intended layering
read backwards, ``ui`` reaching into ``cli`` — until the session engine
moved to ``aida.core.session``; ``aida.cli.chat`` re-exports the same names
so nothing else needed to change.)
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
            # Phase 9 (run_script/cancel_script_run) surfaced a pre-existing
            # gap here: closing the loop right after run_forever() returns,
            # with a still-pending task (e.g. a scheduled coroutine whose
            # create_subprocess_exec hadn't finished when stop() was called)
            # abandons that task mid-await — Python's GC then finalizes it
            # at some arbitrary later point, surfacing as "coroutine ignored
            # GeneratorExit" attributed to whatever unrelated test happens
            # to be running when that GC finally runs. Cancelling pending
            # tasks and giving the loop one more short, *bounded* run lets
            # that cancellation usually propagate cleanly — bounded because
            # an unconditional run_until_complete(gather(...)) here once
            # hung the entire suite when some other feature's pending task
            # didn't unwind on cancellation as quickly as expected; a stuck
            # task delaying shutdown by at most this timeout is a much
            # smaller risk than one hanging it forever.
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                with contextlib.suppress(TimeoutError):
                    self.loop.run_until_complete(
                        asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=2.0)
                    )
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
    # PLAN.md §1.3 / planning/context_management.md §3.4: the "Compact
    # Conversation" menu action's failure/no-op path — success is
    # deliberately *not* a separate signal, see compact_context's docstring.
    compaction_failed = Signal(str)  # message: an error, or "nothing to compact yet"
    # Phase 6 (Phase 11: tri-state): emitted from the background asyncio
    # thread whenever a SafetyGuard-gated tool needs an answer. The second
    # argument is a plain concurrent.futures.Future the receiver
    # (MainWindow, on the Qt thread) must resolve with a ConfirmAnswer by
    # calling future.set_result(...) — see
    # ChatBridge._confirm_interactive's docstring for why a plain Future
    # (not a Qt signal-based reply) is what bridges the two threads here.
    confirmation_requested = Signal(object, object)  # (ConfirmationRequest, concurrent.futures.Future[ConfirmAnswer])
    # Phase 7: MCP management dialog live-control signals. A single
    # "changed"/"failed" pair per action rather than one signal per verb
    # (start/stop/restart/register/unregister) — the dialog just refreshes
    # its whole server list on any of them, the same "re-render from
    # current state" pattern MainWindow already uses for session_ready.
    mcp_server_status_changed = Signal(str)  # server name
    mcp_server_action_failed = Signal(str, str)  # (server name, error message)
    mcp_connection_tested = Signal(str, object)  # (server name, ConnectionTestResult)
    # Phase 8: Knowledge management dialog build/update — same "one
    # finished/failed pair, dialog re-renders from current state" shape as
    # the MCP live-control signals above. Runs on the background loop
    # (embedding calls are real network I/O) so the Qt thread never blocks
    # on a rebuild of a large corpus.
    kb_ingest_finished = Signal(str, object)  # (kb name, IngestResult)
    kb_ingest_failed = Signal(str, str)  # (kb name, error message)
    # U2: "Test" button in the provider/embedding profile editor —
    # validate_profile/validate_embedding_profile are real network pings
    # (a model list call, or a 1-token completion), so they run on the
    # background loop the same way MCP's test_mcp_connection does. One
    # signal per profile kind rather than a shared one: a ProfileValidation
    # for a chat profile and one for an embedding profile aren't
    # interchangeable to a dialog that has two separate lists to refresh.
    profile_validated = Signal(str, object)  # (profile name, ProfileValidation)
    embedding_profile_validated = Signal(str, object)  # (profile name, ProfileValidation)
    # Phase 9: code editor Run/Kill — same "one finished/failed pair" shape
    # as the KB ingest signals above. Runs on the background loop (a real
    # subprocess) so the Qt thread never blocks while a script runs.
    script_run_finished = Signal(object)  # RunResult
    script_run_failed = Signal(str)  # error message

    def __init__(self, loop_thread: AsyncLoopThread, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop_thread = loop_thread
        self.session: ChatSession | None = None
        self.mcp_manager: McpManager | None = None
        # Captured in start() from the Settings passed there — used only by
        # _ensure_mcp_manager()'s live-add-server path below, which (unlike
        # start_session) builds its own McpManager directly and would
        # otherwise miss the scratch-folder wiring (see aida.core.session
        # and aida.mcp.manager for why every MCP subprocess gets one).
        self._scratch_dir: Path | None = None
        # Phase 11 ("Allow for this chat"): one RememberingConfirm per
        # bridge, lazily built and reused by both start() and
        # _ensure_mcp_manager() below — never a fresh instance per call, or
        # a remembered approval from one wouldn't be visible to the other,
        # and a second McpManager built by _ensure_mcp_manager() would
        # silently fragment the chat's remembered-approvals state. Dies
        # with this bridge (a New Chat/resume/workspace switch always
        # builds a brand-new ChatBridge), which is what makes "remember for
        # this chat, not beyond" true with no extra lifetime plumbing.
        self._remembering_confirm: RememberingConfirm | None = None
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
        # The in-flight turn, for the same reason `_start_future` exists:
        # shutdown() has to be able to *finish* with it rather than walk
        # away. A bridge retired mid-turn (the user hits "New Chat" while an
        # MCP plot is running) used to keep streaming — its remaining events
        # rendered into the new session's chat panel and flipped the new
        # input box's busy state — and its ChatSession was closed out from
        # under a turn that was still using it.
        self._turn_future: concurrent.futures.Future | None = None
        # Phase 9 code editor: the live subprocess of whatever script is
        # currently running via run_script, so cancel_script_run has
        # something to kill. None whenever nothing is running.
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

        Unless the caller already supplied one, ``confirm_callback`` is
        defaulted to ``self._remembering_confirm_callback()`` — wrapping
        ``self._confirm_interactive`` (a real modal dialog on the Qt
        thread) so an "Allow for this chat" answer is remembered for the
        rest of this bridge's life, per PLAN.md's "GUI: confirmation flow
        uses a real dialog" requirement (the CLI's own default,
        ``aida.cli.chat.cli_confirm``, would just block invisibly on stdin
        here since the GUI has no terminal)."""
        start_session_kwargs.setdefault("confirm_callback", self._remembering_confirm_callback())
        self._scratch_dir = ensure_scratch_dir(settings.app.scratch_dir)
        self._start_future = asyncio.run_coroutine_threadsafe(
            self._start(settings, start_session_kwargs), self._loop_thread.loop
        )

    def _remembering_confirm_callback(self) -> RememberingConfirm:
        """The one ``RememberingConfirm`` this bridge ever hands out — see
        its docstring on ``self._remembering_confirm`` in ``__init__`` for
        why it must be a single shared instance rather than one per call
        site."""
        if self._remembering_confirm is None:
            self._remembering_confirm = RememberingConfirm(self._confirm_interactive)
        return self._remembering_confirm

    async def _confirm_interactive(self, request: ConfirmationRequest) -> ConfirmAnswer:
        """The GUI's raw, interactive ``RawConfirmCallback`` (Phase 6; made
        tri-state in Phase 11). Runs on the background asyncio thread (it's
        called from inside a tool coroutine that ``SafetyGuard`` is
        awaiting) but the actual decision has to come from a real modal
        dialog on the Qt thread. Bridges the two via a plain
        ``concurrent.futures.Future`` (thread-safe by design, unlike an
        ``asyncio.Future``): emitting ``confirmation_requested`` onto a
        Qt-thread-owned receiver is automatically a thread-safe queued
        delivery (same mechanism ``event_received`` already relies on — see
        this module's docstring), and ``asyncio.wrap_future`` lets this
        coroutine ``await`` the plain ``Future`` the Qt-side handler
        resolves once the user answers the dialog.

        Never handed to ``SafetyGuard``/``McpManager`` directly — always
        wrapped by ``_remembering_confirm_callback()`` first, which turns
        this tri-state ``ConfirmAnswer`` back into the plain bool every
        ``ConfirmCallback`` consumer expects."""
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

    @property
    def is_busy(self) -> bool:
        """Whether a turn is in flight right now.

        ``_turn_future`` is set on the Qt thread by ``send`` and cleared in
        ``_drain``'s ``finally`` — reading it is how a caller distinguishes
        "start a new turn" from "hand this to the turn already running"
        (see ``queue_user_message``) without having to track the
        turn_started/turn_finished signal pair itself.
        """
        return self._turn_future is not None

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

        ``images`` (B1): GUI image attachments, passed straight through to
        ``ChatSession.send`` — see its docstring for what happens to them.
        ``attachment_paths``: the same attachments as paths, so the
        conversation keeps its own copies of the documents a person fed
        into it (``aida.documents.attachments``)."""
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

        Every emit is gated on ``self._closing``: once this bridge is being
        retired its signals must go quiet, because the window has already
        moved on to a different bridge and a stale event would render into
        the *new* session's panel. The generator is still drained to
        completion rather than abandoned mid-iteration — ``ChatSession.send``
        has cleanup in a ``finally`` (dropping the ephemeral retrieval
        message, flushing the transcript) and ``AgentLoop`` answers any
        cancelled tool calls on its way out, so cutting the loop short here
        would skip both. ``shutdown`` sets the cancel flag first, so this
        ends promptly."""
        if not self._closing:
            self.turn_started.emit()
        try:
            async for event in self.session.send(
                user_text,
                images=images,
                attachment_paths=attachment_paths,
                attachment_texts=attachment_texts,
            ):
                if not self._closing:
                    self.event_received.emit(event)
        except Exception as exc:  # noqa: BLE001 - must never crash the loop thread
            if not self._closing:
                self.turn_failed.emit(str(exc))
        finally:
            self._turn_future = None
            if not self._closing:
                self.turn_finished.emit()

    def cancel(self) -> None:
        """Request cancellation of the in-flight turn. ``ChatSession.cancel``
        is a plain synchronous flag-set (see its docstring) — safe to call
        directly from the Qt thread, no scheduling onto the loop needed."""
        if self.session is not None:
            self.session.cancel()

    def queue_user_message(self, text: str) -> bool:
        """Hand the in-flight turn something the user typed while it was
        running; ``True`` if it was queued.

        Same "plain synchronous call from the Qt thread" shape as
        ``cancel()`` — ``AgentLoop``'s queue is a deque touched only by
        append/popleft, so there is nothing to schedule onto the loop
        thread. ``False`` means there was no session or no turn in flight,
        and the caller should send the text as an ordinary turn instead.
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
        """Refused outright while a turn is in flight: the switch closes the
        provider the running ``AgentLoop`` is streaming from. ``MainWindow``
        also disables the selector for the duration of a turn, so this is
        the second line of defense — and the one that covers a switch
        reaching the bridge by any other route."""
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
            # Deliberately broad, and it used to be `except UnknownProfileError`
            # alone. A switch has several other ways to fail —
            # UnknownProviderKindError for a typo'd `kind:`, a missing or
            # unreadable secret, an SDK client constructor rejecting a
            # base_url, SessionBusyError if a turn started between the check
            # above and this coroutine actually running — and each of those
            # used to escape into the background future with nothing
            # awaiting it: the UI simply never heard back, leaving the
            # selector showing a profile the session had not adopted.
            # ChatSession.switch_profile is atomic now, so on *any* of these
            # the session really is untouched — which is exactly what this
            # signal's handler tells the user.
            logger.exception("switching to profile %r failed", name)
            self.profile_switch_failed.emit(str(exc))
            return
        self.profile_switched.emit(name)

    # --- manual compaction (PLAN.md §1.3 / context_management.md §3.4) ----

    def compact_context(self) -> None:
        """"Compact Conversation" menu action — GUI parity with the CLI's
        ``/compact``: summarize everything but the most recent few turns
        right now, regardless of whether the budget is currently exceeded.

        Deliberately reuses ``event_received`` for a successful result
        rather than a dedicated "finished" signal: the ``ContextTrimmed``
        this produces is identical in shape to one an *automatic*
        mid-turn compaction would emit, and ``MainWindow._on_event_received``
        already renders that event (status-bar message, context-fullness
        refresh) — a second code path would just be that same handling
        duplicated. Only the "nothing happened" outcomes (no session, not
        enough history yet, the summarization call itself failed) get their
        own signal, since those have no ``AgentEvent`` to piggyback on."""
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

    # --- MCP server live control (Phase 7 management dialog) ---------------

    def _ensure_mcp_manager(self) -> McpManager:
        """Every live-control action needs an ``McpManager`` to act on, but
        a session with zero MCP servers configured never builds one (lazy
        start — see ``aida.cli.chat.start_session``). Creating one on first
        use here is what lets "Add Server" + "Start" work the very first
        time a server is ever added in a session, with nothing configured
        beforehand."""
        if self.mcp_manager is None:
            # Same confirm channel start_session wires into a manager built
            # at session-start time — a server added live (via the MCP
            # management dialog, before any MCP server had ever been used
            # this session) must not fall back to McpManager's own
            # deny_all default, or a confirm-flagged tool on it would
            # always silently refuse.
            self.mcp_manager = McpManager(
                [],
                confirm_callback=self._remembering_confirm_callback(),
                scratch_dir=self._scratch_dir or ensure_scratch_dir(),
            )
        return self.mcp_manager

    def _drop_server_tools_from_session(self, name: str) -> None:
        """Remove every namespaced tool belonging to ``name`` from the live
        session's tool dict. ``ChatSession.tools`` and ``AgentLoop.tools``
        are the same dict object (``AgentLoop.__init__`` stores the passed
        dict by reference), so this is immediately visible to the running
        agent loop with no other plumbing."""
        if self.session is None:
            return
        prefix = f"{name}{NAMESPACE_SEPARATOR}"
        for key in [k for k in self.session.tools if k.startswith(prefix)]:
            del self.session.tools[key]

    def register_mcp_server(self, config: McpServerConfig) -> None:
        """Make a brand-new (or freshly-edited) server config known to the
        live session's manager, without starting it — the GUI's "Add
        Server"/"Edit" dialogs call this so a subsequent "Start" in the
        same session works without restarting the whole chat session."""
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

    # --- provider/embedding profile validation (U2 management dialog) ------

    def validate_provider_profile(self, profile: ProviderProfile) -> None:
        """"Test" button for one ``ProviderProfile`` — pings the real
        endpoint (see ``aida.providers.profiles.validate_profile``'s
        docstring for what that means per provider kind) on the background
        loop, never blocking the Qt thread."""
        asyncio.run_coroutine_threadsafe(self._validate_provider_profile(profile), self._loop_thread.loop)

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

    # --- knowledge base build/update (Phase 8 management dialog) -----------

    def rebuild_knowledge_base(self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile) -> None:
        """Full re-ingest of ``kb``: every discovered file is re-chunked and
        re-embedded. Takes the configs directly (not a name to look up)
        the same way ``register_mcp_server`` does — the dialog already has
        them from ``settings``, and this keeps the bridge from needing to
        hold a ``Settings`` reference of its own."""
        asyncio.run_coroutine_threadsafe(self._run_kb_ingest(kb, embedding_profile, rebuild=True), self._loop_thread.loop)

    def update_knowledge_base(self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile) -> None:
        """Incremental re-ingest of ``kb``: only files changed since the
        last build/update are re-embedded."""
        asyncio.run_coroutine_threadsafe(self._run_kb_ingest(kb, embedding_profile, rebuild=False), self._loop_thread.loop)

    async def _run_kb_ingest(self, kb: KnowledgeBaseConfig, embedding_profile: EmbeddingProfile, *, rebuild: bool) -> None:
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
            # this catches provider-level failures (auth, network, a
            # misconfigured base_url) that must reach the dialog as a
            # message rather than crash the background loop.
            self.kb_ingest_failed.emit(kb.name, str(exc))
            return
        finally:
            conn.close()
            await embeddings_provider.aclose()

        self.kb_ingest_finished.emit(kb.name, result)

    # --- code editor run/kill (Phase 9) ---------------------------------------

    def run_script(
        self, path: str, args: list[str], *, interpreter: str | None, cwd: str, timeout: float
    ) -> None:
        """Runs ``path`` on the background loop so a real subprocess never
        blocks the Qt thread — same "schedule via run_coroutine_threadsafe,
        report back via a signal" shape as ``rebuild_knowledge_base``."""
        asyncio.run_coroutine_threadsafe(
            self._run_script(path, args, interpreter=interpreter, cwd=cwd, timeout=timeout), self._loop_thread.loop
        )

    def cancel_script_run(self) -> None:
        """Kills whatever script ``run_script`` currently has running, if
        any — a no-op if nothing is running (already finished, or never
        started). The actual ``.kill()`` call happens on the background
        loop thread that owns the ``Process`` object; scheduling it there
        rather than calling it directly from the Qt thread keeps every
        touch of that object on the one thread that's actually driving it."""
        asyncio.run_coroutine_threadsafe(self._cancel_running_script(), self._loop_thread.loop)

    async def _cancel_running_script(self) -> None:
        if self._running_script_proc is not None:
            # terminate_run, not proc.kill(): kill() signals only the
            # process AIDA launched, so a script that had spawned anything
            # of its own (a multiprocessing pool, a plotting backend, its
            # own subprocess) left those running with Stop already spent and
            # nothing in the UI able to reach them. terminate_run signals
            # the whole process group.
            await terminate_run(self._running_script_proc)

    async def _run_script(
        self, path: str, args: list[str], *, interpreter: str | None, cwd: str, timeout: float
    ) -> None:
        def _on_started(proc) -> None:
            self._running_script_proc = proc

        try:
            result = await run_python_script(
                path, args, interpreter=interpreter, cwd=cwd, timeout=timeout, on_started=_on_started
            )
        except OSError as exc:
            self.script_run_failed.emit(str(exc))
            return
        finally:
            self._running_script_proc = None

        self.script_run_finished.emit(result)

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
        # Snapshot before awaiting: _drain clears the attribute in its own
        # `finally`, which can land between the check and the await.
        turn_future, self._turn_future = self._turn_future, None
        if turn_future is not None:
            # Same reasoning as the start above, one step further: a turn
            # still running when this bridge is retired must be *finished
            # with*, not walked away from — otherwise session.aclose()
            # below closes the provider and recorder out from under it.
            # cancel() is the session's own cooperative flag (checked
            # between tool calls), so the turn unwinds through its normal
            # cleanup path rather than being killed mid-await.
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
