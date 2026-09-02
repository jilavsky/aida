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
from collections.abc import Callable
from datetime import datetime

from aida.config.logging_setup import get_logger
from aida.config.settings import ScheduleEntry, Settings, load_settings, load_workflow
from aida.core.headless import build_headless_confirm_callback
from aida.core.proc_lock import try_acquire_scheduler_lock
from aida.core.scheduling import ScheduleConfigError, is_due, parse_schedule_timing
from aida.core.workflows import WorkflowConfigError, run_workflow
from aida.persistence.store import ScheduleRunStore

logger = get_logger("scheduler")

#: (schedule_name, ok, conversation_id, error)
OnRunFinished = Callable[[str, bool, str | None, str | None], None]
OnRunStarted = Callable[[str], None]


async def run_due_schedules(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    on_run_started: OnRunStarted | None = None,
    on_run_finished: OnRunFinished | None = None,
) -> list[str]:
    """One polling tick. Reloads ``settings`` fresh via ``load_settings()``
    when not given explicitly — a GUI edit or hand-edit of ``schedules.yaml``
    between ticks must be picked up without restarting anything. Takes the
    cross-process lock (``aida.core.proc_lock``) for the whole tick; if
    another process holds it, this tick is skipped entirely rather than
    partially run. Returns the names of schedules actually run this tick."""
    now = now or datetime.now()
    ran: list[str] = []
    with try_acquire_scheduler_lock() as acquired:
        if not acquired:
            return ran
        effective_settings = settings or load_settings()
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
                if not is_due(parsed, last_fired_at=last_fired_at, now=now):
                    continue

                ran.append(name)
                if on_run_started is not None:
                    on_run_started(name)
                await _fire(name, entry, effective_settings, now=now, run_store=run_store, on_run_finished=on_run_finished)
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
        logger.warning("schedule %r references workflow %r, which doesn't exist: %s", name, entry.workflow, exc)
        run_store.record_run(schedule_name=name, fired_at=fired_at, status="config_error", error=str(exc))
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
        run_store.record_run(schedule_name=name, fired_at=fired_at, status="config_error", error=str(exc))
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
    run updates last-fired/status exactly like a normal due tick would."""
    run_store = ScheduleRunStore()
    try:
        await _fire(name, entry, settings, now=datetime.now(), run_store=run_store, on_run_finished=on_run_finished)
    finally:
        run_store.close()


async def scheduler_loop(
    *,
    poll_interval_seconds: float = 30.0,
    on_run_started: OnRunStarted | None = None,
    on_run_finished: OnRunFinished | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Runs ``run_due_schedules`` forever, sleeping ``poll_interval_seconds``
    between ticks. The one function the GUI's background asyncio task
    (``aida.ui.qt.bridge.ChatBridge``) and ``aida schedule watch`` both
    drive — see this module's docstring.

    Ends when ``stop_event`` is set (checked between ticks, and interrupts
    the sleep immediately rather than waiting out the rest of the interval)
    or when the enclosing task is cancelled — there is no other exit path,
    by design: a scheduler that stops on an unhandled exception from one
    bad tick would silently stop watching every other schedule too, so a
    tick's own failures are caught and recorded per-schedule inside
    ``run_due_schedules`` instead of ever propagating here."""
    while True:
        await run_due_schedules(on_run_started=on_run_started, on_run_finished=on_run_finished)
        if stop_event is not None:
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
                return  # stop_event was set before the interval elapsed
            except TimeoutError:
                continue  # normal case: interval elapsed, poll again
        await asyncio.sleep(poll_interval_seconds)


__all__ = ["OnRunFinished", "OnRunStarted", "fire_schedule_now", "run_due_schedules", "scheduler_loop"]
