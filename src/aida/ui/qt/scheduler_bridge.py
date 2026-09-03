"""Runs the in-app scheduler (Phase 10,
planning/phase10_scheduling_design.md §6) on the shared ``AsyncLoopThread``
as a background task for the life of the app process.

Deliberately a separate ``QObject`` from ``ChatBridge``, not a method on
it: ``ChatBridge`` owns exactly *one* interactive ``ChatSession`` and is
torn down and replaced on every "New Chat" / workspace switch / profile
switch (see its own docstring and ``MainWindow._restart_session``). The
scheduler must keep running across every one of those — it opens its own
``ChatSession`` per fired schedule via ``aida.core.workflows.run_workflow``
and never touches the interactive one at all — so it needs a lifetime tied
to the *window* (one instance for the app's whole run), not to whichever
bridge happens to be current. ``MainWindow`` constructs one
``SchedulerBridge`` once and stops it once, in ``closeEvent``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib

from aida.config.logging_setup import get_logger
from aida.config.settings import ScheduleEntry, Settings
from aida.core.scheduler_runtime import UserActivityState, fire_schedule_now, scheduler_loop
from aida.ui.qt._qt import QObject, Signal
from aida.ui.qt.bridge import AsyncLoopThread

logger = get_logger("ui.scheduler_bridge")


class SchedulerBridge(QObject):
    """Starts ``aida.core.scheduler_runtime.scheduler_loop`` on
    ``loop_thread`` and re-emits its per-run callbacks as Qt signals — the
    same "background asyncio thread calls a plain callback, the callback
    emits a signal, Qt's queued-connection delivery makes that thread-safe
    automatically" pattern ``aida.ui.qt.bridge`` documents at its own top."""

    run_started = Signal(str)  # schedule name
    run_finished = Signal(str, bool, str, str)  # name, ok, conversation_id, error
    #: ``{schedule name: reason}`` for everything currently held back
    #: waiting for the user to be idle — a whole snapshot per tick, so a
    #: listener can just replace whatever it was showing (see
    #: ``aida.core.scheduler_runtime.scheduler_loop``'s docstring).
    deferred_changed = Signal(object)  # dict[str, str]

    def __init__(
        self,
        loop_thread: AsyncLoopThread,
        *,
        poll_interval_seconds: float = 30.0,
        defer_to_user: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._loop_thread = loop_thread
        self._poll_interval_seconds = poll_interval_seconds
        #: Written by the Qt thread (``MainWindow`` keeps it current), read
        #: by the background loop thread each tick. Owned *here* rather
        #: than by ``MainWindow`` on purpose: the loop must never hold a
        #: reference to a widget it could outlive — see
        #: ``UserActivityState``'s docstring for the segfault that caused.
        self.activity = UserActivityState()
        #: ``False`` disables the whole politeness layer (used by tests
        #: that want a tick to fire immediately); the schedule still runs
        #: through the identical code path either way.
        self._should_defer = self.activity.should_defer if defer_to_user else None
        self._stop_event: asyncio.Event | None = None
        self._future: concurrent.futures.Future | None = None

    def start(self) -> None:
        """Idempotent: a second call while already running is a no-op —
        ``MainWindow`` only ever calls this once, but nothing here assumes
        that stays true."""
        if self._future is not None:
            return
        self._stop_event = asyncio.Event()
        self._future = asyncio.run_coroutine_threadsafe(self._run(), self._loop_thread.loop)

    async def _run(self) -> None:
        try:
            await scheduler_loop(
                poll_interval_seconds=self._poll_interval_seconds,
                on_run_started=self._emit_started,
                on_run_finished=self._emit_finished,
                should_defer=self._should_defer,
                on_deferred_changed=self._emit_deferred_changed,
                stop_event=self._stop_event,
            )
        except Exception:  # noqa: BLE001 - must never crash the loop thread
            logger.exception("scheduler loop crashed")

    def _emit_started(self, name: str) -> None:
        self.run_started.emit(name)

    def _emit_deferred_changed(self, deferred: dict[str, str]) -> None:
        self.deferred_changed.emit(deferred)

    def _emit_finished(self, name: str, ok: bool, conversation_id: str | None, error: str | None) -> None:
        self.run_finished.emit(name, ok, conversation_id or "", error or "")

    def run_now(self, name: str, entry: ScheduleEntry, settings: Settings) -> None:
        """Fires one schedule immediately, regardless of whether it's due —
        what the schedule management dialog's "Run Now" button calls.
        Goes through the exact same ``aida.core.scheduler_runtime.
        fire_schedule_now`` a normal due tick uses, so it updates
        ``ScheduleRunStore`` and emits the same ``run_started``/
        ``run_finished`` signals a scheduled fire would — the dialog and
        the failure indicator both just react to those, with no separate
        "forced run" code path to keep in sync."""
        self.run_started.emit(name)
        asyncio.run_coroutine_threadsafe(
            fire_schedule_now(name, entry, settings, on_run_finished=self._emit_finished),
            self._loop_thread.loop,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Signals ``scheduler_loop`` to return after its current tick and
        blocks until it does (or ``timeout``) — call this once, from the Qt
        thread, before the app quits. Safe to call even if ``start()`` was
        never called."""
        if self._stop_event is not None:
            # Event.set() is a plain synchronous method, but the waiters it
            # wakes live on the loop thread — call_soon_threadsafe is what
            # makes crossing that thread boundary safe, the same idiom
            # AsyncLoopThread.stop() uses for loop.stop().
            self._loop_thread.loop.call_soon_threadsafe(self._stop_event.set)
        if self._future is not None:
            with contextlib.suppress(Exception):
                self._future.result(timeout=timeout)
        self._future = None


__all__ = ["SchedulerBridge"]
