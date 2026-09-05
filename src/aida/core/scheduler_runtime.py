"""Drives ``aida.core.scheduling`` and ``aida.core.workflows`` against real
config, the real clock, and ``aida.persistence.store.ScheduleRunStore`` —
the one function both the GUI's background task and ``aida schedule watch``
call (planning/phase10_scheduling_design.md §6: "the in-app trigger and the
OS/CLI trigger must call the same code path").

Timestamps in this module are deliberately **naive local time**, not the
UTC-everywhere convention the rest of ``aida.persistence`` uses
(``aida.persistence.recorder._now_iso``): ``schedules.yaml``'s ``at:
"07:00"`` is inherently a wall-clock concept — a beamline user means 7am
where they are sitting, not 7am UTC — so ``aida.core.scheduling.is_due``
compares naive local ``datetime``s throughout, and ``schedule_runs.fired_at``
(written here, read back here) has to use the same clock to compare
against, or every comparison would need an unnecessary timezone
conversion. This is self-contained to scheduling; nothing else reads these
timestamps.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from aida.config.logging_setup import get_logger
from aida.config.settings import ScheduleEntry, Settings, load_settings, load_workflow
from aida.core.headless import build_headless_confirm_callback
from aida.core.proc_lock import try_acquire_scheduler_lock
from aida.core.scheduling import ScheduleConfigError, due_since, parse_schedule_timing
from aida.core.workflows import WorkflowConfigError, run_workflow
from aida.persistence.store import ScheduleRunStore

logger = get_logger("scheduler")

#: (schedule_name, ok, conversation_id, error)
OnRunFinished = Callable[[str, bool, str | None, str | None], None]
OnRunStarted = Callable[[str], None]
#: (schedule_name, human-readable reason it is waiting)
OnRunDeferred = Callable[[str, str], None]


@dataclass(frozen=True)
class DeferralRequest:
    """Why a due schedule should not start right now.

    ``hard`` is the difference between "it would be rude to start" and "it
    is not safe to start": a soft deferral (the user finished a step
    moments ago, or has half a prompt typed) is waived once a job has been
    waiting longer than ``AppConfig.scheduler_max_defer_seconds``, because
    a report that silently never appeared is a worse failure than a mildly
    untimely one. A hard deferral — a turn actually streaming right now —
    is never waived at any cap, since starting a second session on top of a
    live one is the exact collision this whole mechanism exists to prevent.
    """

    reason: str
    hard: bool = False


#: Returns a ``DeferralRequest`` to hold a due job back, or ``None`` to let
#: it run. Supplied only by callers that have a user to be considerate of
#: (the GUI); ``aida schedule watch`` has no user session in its process
#: and passes nothing, so it never defers.
DeferCheck = Callable[[], "DeferralRequest | None"]


def evaluate_user_deferral(
    *,
    turn_in_flight: bool,
    has_unsent_text: bool,
    idle_seconds: float,
    quiet_period_seconds: float,
) -> DeferralRequest | None:
    """The in-app scheduler's politeness policy, as a pure function so it
    is testable without Qt.

    ``idle_seconds`` is measured from the *later* of "last turn ended" and
    "last keystroke in the input box" — composing a long prompt has to
    count as activity, or a job could start in the middle of the user
    typing it.
    """
    if turn_in_flight:
        return DeferralRequest("a turn is running", hard=True)
    if has_unsent_text:
        return DeferralRequest("you have unsent text in the input box")
    if idle_seconds < quiet_period_seconds:
        remaining = max(0, round(quiet_period_seconds - idle_seconds))
        return DeferralRequest(f"waiting {remaining}s for you to finish")
    return None


class UserActivityState:
    """What the user is doing, shared between the Qt thread (which writes
    it) and the scheduler's background loop thread (which reads it once per
    tick via ``should_defer``).

    Deliberately a plain Python object with plain attributes, and
    deliberately *not* holding a reference to any widget. The obvious
    alternative — handing the scheduler a bound method of ``MainWindow`` —
    is a real hazard rather than a style preference: it puts a live
    reference to the window on the background thread, so a tick landing
    while that window is being torn down calls into a half-destroyed
    ``QObject``. That reproduced immediately as a hard interpreter
    segfault during GC (not a Python exception — nothing can catch it).
    Attribute reads and writes here are atomic enough under the GIL for
    this purpose: the worst a torn read can cost is one tick's decision
    being a moment stale, which the next tick corrects.
    """

    def __init__(self, *, quiet_period_seconds: float = 300.0) -> None:
        self.turn_in_flight = False
        self.has_unsent_text = False
        self.quiet_period_seconds = quiet_period_seconds
        self.last_activity_monotonic = time.monotonic()

    def note_activity(self) -> None:
        """ "The user is still working" — resets the quiet period."""
        self.last_activity_monotonic = time.monotonic()

    def should_defer(self) -> DeferralRequest | None:
        """A ``DeferCheck``: bound and handed to ``scheduler_loop``. Safe
        to call from any thread — see this class's docstring."""
        return evaluate_user_deferral(
            turn_in_flight=self.turn_in_flight,
            has_unsent_text=self.has_unsent_text,
            idle_seconds=time.monotonic() - self.last_activity_monotonic,
            quiet_period_seconds=self.quiet_period_seconds,
        )


async def run_due_schedules(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    on_run_started: OnRunStarted | None = None,
    on_run_finished: OnRunFinished | None = None,
    should_defer: DeferCheck | None = None,
    on_run_deferred: OnRunDeferred | None = None,
) -> list[str]:
    """One polling tick. Reloads ``settings`` fresh via ``load_settings()``
    when not given explicitly — a GUI edit or hand-edit of ``schedules.yaml``
    between ticks must be picked up without restarting anything. Takes the
    cross-process lock (``aida.core.proc_lock``) for the whole tick; if
    another process holds it, this tick is skipped entirely rather than
    partially run. Returns the names of schedules actually run this tick.

    A due schedule that ``should_defer`` holds back is *deferred, not
    skipped*: nothing is written to ``ScheduleRunStore``, so
    ``due_since`` still reports it as due on the next tick and it runs at
    the first opportunity. That falls out of the existing design — there is
    no "pending" state to persist anywhere — and it is also why the
    deferral cap can be measured statelessly, as ``now - due_since``.
    """
    now = now or datetime.now()
    ran: list[str] = []
    with try_acquire_scheduler_lock() as acquired:
        if not acquired:
            return ran
        effective_settings = settings or load_settings()
        max_defer = effective_settings.app.scheduler_max_defer_seconds
        run_store = ScheduleRunStore()
        try:
            for name, entry in sorted(effective_settings.schedules.schedules.items()):
                if not entry.enabled:
                    continue
                try:
                    parsed = parse_schedule_timing(at=entry.at, every=entry.every)
                except ScheduleConfigError as exc:
                    logger.warning("schedule %r has an invalid at/every — skipping: %s", name, exc)
                    continue

                last_run = run_store.last_run(name)
                last_fired_at = datetime.fromisoformat(last_run.fired_at) if last_run else None
                due_at = due_since(parsed, last_fired_at=last_fired_at, now=now)
                if due_at is None:
                    continue

                deferral = should_defer() if should_defer is not None else None
                if deferral is not None:
                    overdue_seconds = (now - due_at).total_seconds()
                    past_cap = max_defer > 0 and overdue_seconds >= max_defer
                    if deferral.hard or not past_cap:
                        logger.debug("schedule %r deferred: %s", name, deferral.reason)
                        if on_run_deferred is not None:
                            on_run_deferred(name, deferral.reason)
                        continue
                    logger.info(
                        "schedule %r has been waiting %.0fs (cap %ds) — running despite: %s",
                        name,
                        overdue_seconds,
                        max_defer,
                        deferral.reason,
                    )

                ran.append(name)
                if on_run_started is not None:
                    on_run_started(name)
                await _fire(
                    name,
                    entry,
                    effective_settings,
                    now=now,
                    run_store=run_store,
                    on_run_finished=on_run_finished,
                )
        finally:
            run_store.close()
    return ran


async def _fire(
    name: str,
    entry: ScheduleEntry,
    settings: Settings,
    *,
    now: datetime,
    run_store: ScheduleRunStore,
    on_run_finished: OnRunFinished | None,
) -> None:
    fired_at = now.isoformat()
    try:
        workflow = load_workflow(entry.workflow)
    except FileNotFoundError as exc:
        logger.warning(
            "schedule %r references workflow %r, which doesn't exist: %s", name, entry.workflow, exc
        )
        run_store.record_run(
            schedule_name=name, fired_at=fired_at, status="config_error", error=str(exc)
        )
        if on_run_finished is not None:
            on_run_finished(name, False, None, str(exc))
        return

    confirm_callback = build_headless_confirm_callback(
        yes_in_allowed=entry.yes_in_allowed,
        preapproved_tools=set(entry.preapproved_tools) | set(workflow.preapproved_tools),
    )
    try:
        result = await run_workflow(
            settings,
            workflow,
            var_overrides=entry.vars,
            confirm_callback=confirm_callback,
            origin="schedule",
        )
    except WorkflowConfigError as exc:
        logger.warning("schedule %r failed to start: %s", name, exc)
        run_store.record_run(
            schedule_name=name, fired_at=fired_at, status="config_error", error=str(exc)
        )
        if on_run_finished is not None:
            on_run_finished(name, False, None, str(exc))
        return

    run_store.record_run(
        schedule_name=name,
        fired_at=fired_at,
        status="ok" if result.ok else "failed",
        conversation_id=result.conversation_id,
        error=result.error,
    )
    if on_run_finished is not None:
        on_run_finished(name, result.ok, result.conversation_id, result.error)


async def fire_schedule_now(
    name: str,
    entry: ScheduleEntry,
    settings: Settings,
    *,
    on_run_finished: OnRunFinished | None = None,
) -> None:
    """Runs one schedule's workflow immediately, regardless of whether the
    clock says it's due — what ``aida schedule run NAME`` and the GUI's
    "Run now" button both call, per the design doc's "the same code path
    either way" rule. Still goes through ``ScheduleRunStore``, so a forced
    run updates last-fired/status exactly like a normal due tick would.

    Deliberately *not* subject to ``should_defer``: this is an explicit
    user action ("run it now"), so waiting for the user to be idle would be
    absurd. It does take the same cross-process lock a polling tick does,
    though — without it a Run Now could land on top of a scheduled run
    already in progress, which is the one overlap the lock exists to
    prevent."""
    with try_acquire_scheduler_lock() as acquired:
        if not acquired:
            message = "another scheduled run is already in progress"
            logger.info("forced run of schedule %r refused: %s", name, message)
            if on_run_finished is not None:
                on_run_finished(name, False, None, message)
            return
        run_store = ScheduleRunStore()
        try:
            await _fire(
                name,
                entry,
                settings,
                now=datetime.now(),
                run_store=run_store,
                on_run_finished=on_run_finished,
            )
        finally:
            run_store.close()


async def scheduler_loop(
    *,
    poll_interval_seconds: float = 30.0,
    on_run_started: OnRunStarted | None = None,
    on_run_finished: OnRunFinished | None = None,
    should_defer: DeferCheck | None = None,
    on_deferred_changed: Callable[[dict[str, str]], None] | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Runs ``run_due_schedules`` forever, sleeping ``poll_interval_seconds``
    between ticks. The one function the GUI's background asyncio task
    (``aida.ui.qt.scheduler_bridge.SchedulerBridge``) and ``aida schedule
    watch`` both drive — see this module's docstring.

    ``on_deferred_changed`` receives ``{name: reason}`` for everything held
    back, once per tick — a whole authoritative snapshot rather than
    per-schedule add/remove events, so a UI showing "N jobs waiting" is
    self-correcting: a schedule deleted or disabled while waiting simply
    stops appearing, with no stale entry to clean up.

    Ends when ``stop_event`` is set (checked between ticks, and interrupts
    the sleep immediately rather than waiting out the rest of the interval)
    or when the enclosing task is cancelled — there is no other exit path,
    by design: a scheduler that stops on an unhandled exception from one
    bad tick would silently stop watching every other schedule too, so a
    tick's own failures are caught and recorded per-schedule inside
    ``run_due_schedules`` instead of ever propagating here."""
    while True:
        deferred: dict[str, str] = {}
        await run_due_schedules(
            on_run_started=on_run_started,
            on_run_finished=on_run_finished,
            should_defer=should_defer,
            on_run_deferred=deferred.__setitem__,
        )
        if on_deferred_changed is not None:
            on_deferred_changed(deferred)
        if stop_event is not None:
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
                return  # stop_event was set before the interval elapsed
            except TimeoutError:
                continue  # normal case: interval elapsed, poll again
        await asyncio.sleep(poll_interval_seconds)


__all__ = [
    "DeferCheck",
    "DeferralRequest",
    "OnRunDeferred",
    "OnRunFinished",
    "OnRunStarted",
    "UserActivityState",
    "evaluate_user_deferral",
    "fire_schedule_now",
    "run_due_schedules",
    "scheduler_loop",
]
