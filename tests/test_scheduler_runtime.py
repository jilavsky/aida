"""Tests for aida.core.scheduler_runtime — the function both the GUI's
background task and ``aida schedule watch`` drive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aida.config.settings import (
    ProviderProfile,
    ScheduleEntry,
    SchedulesConfig,
    Settings,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
    save_workflow,
)
from aida.core.proc_lock import try_acquire_scheduler_lock
from aida.core.scheduler_runtime import run_due_schedules, scheduler_loop
from aida.persistence.store import ScheduleRunStore
from aida.providers.mock import MockProvider, MockTurn


def _settings(schedules: SchedulesConfig | None = None) -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={
            "use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")
        }
    )
    if schedules is not None:
        settings.schedules = schedules
    return settings


def _workflow(name: str = "daily") -> None:
    save_workflow(WorkflowConfig(name=name, workspace="use-ws", steps=[WorkflowStep(prompt="go")]))


@pytest.mark.asyncio
async def test_disabled_schedule_never_runs(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(
            schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1m", enabled=False)}
        )
    )

    ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []


@pytest.mark.asyncio
async def test_never_fired_every_schedule_runs_immediately(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )

    finished = []
    ran = await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 10, 0),
        on_run_finished=lambda *args: finished.append(args),
    )

    assert ran == ["s"]
    assert finished == [("s", True, finished[0][2], None)]
    assert finished[0][2] is not None  # conversation_id


@pytest.mark.asyncio
async def test_not_due_schedule_is_skipped(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="4h")})
    )
    now = datetime(2026, 9, 2, 10, 0)

    first = await run_due_schedules(settings, now=now)
    assert first == ["s"]

    second = await run_due_schedules(settings, now=now + timedelta(hours=1))  # only 1h later
    assert second == []


@pytest.mark.asyncio
async def test_catch_up_fires_once_not_repeatedly(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="done")] * 5),
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="4h")})
    )
    now = datetime(2026, 9, 2, 10, 0)

    await run_due_schedules(settings, now=now)  # fires (never fired before)
    ran_again = await run_due_schedules(settings, now=now + timedelta(days=3))  # a big gap
    assert ran_again == ["s"]  # fires exactly once for the whole gap
    ran_immediately_after = await run_due_schedules(settings, now=now + timedelta(days=3))
    assert ran_immediately_after == []  # not fired twice for the same gap


@pytest.mark.asyncio
async def test_malformed_at_every_is_skipped_without_crashing(
    monkeypatch, aida_home: Path, records_home: Path
):
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily")})
    )  # neither set

    ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []


@pytest.mark.asyncio
async def test_missing_workflow_records_config_error_and_calls_on_run_finished(
    aida_home: Path, records_home: Path
):
    settings = _settings(
        SchedulesConfig(
            schedules={"s": ScheduleEntry(name="s", workflow="does-not-exist", every="1h")}
        )
    )

    finished = []
    ran = await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 10, 0),
        on_run_finished=lambda *args: finished.append(args),
    )

    assert ran == ["s"]
    assert finished[0][1] is False  # ok=False
    assert finished[0][2] is None  # no conversation created
    assert "does-not-exist" in finished[0][3]

    store = ScheduleRunStore()
    last = store.last_run("s")
    store.close()
    assert last.status == "config_error"


@pytest.mark.asyncio
async def test_workflow_agent_error_records_failed_status(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(error="boom")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )

    finished = []
    await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 10, 0),
        on_run_finished=lambda *args: finished.append(args),
    )

    assert finished[0][1] is False
    assert "boom" in finished[0][3]
    store = ScheduleRunStore()
    last = store.last_run("s")
    store.close()
    assert last.status == "failed"


@pytest.mark.asyncio
async def test_on_run_started_called_before_on_run_finished(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )

    events = []
    await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 10, 0),
        on_run_started=lambda name: events.append(("started", name)),
        on_run_finished=lambda *args: events.append(("finished", args[0])),
    )

    assert events == [("started", "s"), ("finished", "s")]


@pytest.mark.asyncio
async def test_tick_skipped_entirely_when_lock_held_elsewhere(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )

    from aida.config.paths import scheduler_lock_path

    with try_acquire_scheduler_lock(scheduler_lock_path()) as held:
        assert held is True
        ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []  # the whole tick was skipped, not partially run


@pytest.mark.asyncio
async def test_scheduler_loop_runs_a_tick_and_stops_on_stop_event(
    monkeypatch, aida_home: Path, records_home: Path
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", lambda: settings)

    stop_event = asyncio.Event()
    finished = []

    def _on_finished(*args):
        finished.append(args)
        stop_event.set()

    await asyncio.wait_for(
        scheduler_loop(
            poll_interval_seconds=60, on_run_finished=_on_finished, stop_event=stop_event
        ),
        timeout=5.0,
    )

    assert len(finished) == 1
    assert finished[0][0] == "s"


# --- deferring to the user (Phase 10) --------------------------------------


def test_evaluate_user_deferral_allows_an_idle_user():
    from aida.core.scheduler_runtime import evaluate_user_deferral

    assert (
        evaluate_user_deferral(
            turn_in_flight=False, has_unsent_text=False, idle_seconds=600, quiet_period_seconds=300
        )
        is None
    )


def test_evaluate_user_deferral_running_turn_is_hard():
    from aida.core.scheduler_runtime import evaluate_user_deferral

    deferral = evaluate_user_deferral(
        turn_in_flight=True, has_unsent_text=False, idle_seconds=99_999, quiet_period_seconds=300
    )
    assert deferral is not None
    assert deferral.hard is True


def test_evaluate_user_deferral_unsent_text_is_soft():
    from aida.core.scheduler_runtime import evaluate_user_deferral

    deferral = evaluate_user_deferral(
        turn_in_flight=False, has_unsent_text=True, idle_seconds=99_999, quiet_period_seconds=300
    )
    assert deferral is not None
    assert deferral.hard is False


def test_evaluate_user_deferral_quiet_period_is_soft_and_reports_remaining():
    from aida.core.scheduler_runtime import evaluate_user_deferral

    deferral = evaluate_user_deferral(
        turn_in_flight=False, has_unsent_text=False, idle_seconds=60, quiet_period_seconds=300
    )
    assert deferral is not None
    assert deferral.hard is False
    assert "240" in deferral.reason


def test_evaluate_user_deferral_zero_quiet_period_never_waits():
    from aida.core.scheduler_runtime import evaluate_user_deferral

    assert (
        evaluate_user_deferral(
            turn_in_flight=False, has_unsent_text=False, idle_seconds=0, quiet_period_seconds=0
        )
        is None
    )


def test_user_activity_state_note_activity_resets_the_clock():
    from aida.core.scheduler_runtime import UserActivityState

    state = UserActivityState(quiet_period_seconds=300)
    state.last_activity_monotonic -= 10_000
    assert state.should_defer() is None

    state.note_activity()
    assert state.should_defer() is not None


@pytest.mark.asyncio
async def test_due_schedule_is_deferred_not_skipped(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Deferral writes nothing to ScheduleRunStore, so the job stays due
    and runs at the first opportunity rather than being lost."""
    from aida.core.scheduler_runtime import DeferralRequest

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )
    now = datetime(2026, 9, 2, 10, 0)

    deferred: dict[str, str] = {}
    ran = await run_due_schedules(
        settings,
        now=now,
        should_defer=lambda: DeferralRequest("busy"),
        on_run_deferred=deferred.__setitem__,
    )
    assert ran == []
    assert deferred == {"s": "busy"}

    store = ScheduleRunStore()
    assert store.last_run("s") is None  # nothing recorded — still due
    store.close()

    # Same tick conditions, but the user is now idle: it runs.
    ran_now = await run_due_schedules(settings, now=now, should_defer=lambda: None)
    assert ran_now == ["s"]


@pytest.mark.asyncio
async def test_soft_deferral_is_waived_past_the_cap(
    monkeypatch, aida_home: Path, records_home: Path
):
    from aida.core.scheduler_runtime import DeferralRequest

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", at="07:00")})
    )
    settings.app.scheduler_max_defer_seconds = 3600

    # Due since 07:00; it is now 09:00, so it has waited 2h — past the cap.
    ran = await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 9, 0),
        should_defer=lambda: DeferralRequest("you just typed something"),
    )
    assert ran == ["s"]


@pytest.mark.asyncio
async def test_hard_deferral_is_never_waived_past_the_cap(
    monkeypatch, aida_home: Path, records_home: Path
):
    """A live turn blocks a run at any age — starting a second session on
    top of a streaming one is the collision this all exists to prevent."""
    from aida.core.scheduler_runtime import DeferralRequest

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", at="07:00")})
    )
    settings.app.scheduler_max_defer_seconds = 3600

    ran = await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 9, 0),  # 2h overdue, well past the cap
        should_defer=lambda: DeferralRequest("a turn is running", hard=True),
    )
    assert ran == []


@pytest.mark.asyncio
async def test_cap_of_zero_defers_indefinitely(monkeypatch, aida_home: Path, records_home: Path):
    from aida.core.scheduler_runtime import DeferralRequest

    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", at="07:00")})
    )
    settings.app.scheduler_max_defer_seconds = 0  # never force

    ran = await run_due_schedules(
        settings, now=datetime(2026, 9, 3, 9, 0), should_defer=lambda: DeferralRequest("still busy")
    )
    assert ran == []


@pytest.mark.asyncio
async def test_fire_schedule_now_is_refused_while_the_lock_is_held(
    aida_home: Path, records_home: Path
):
    """Run Now must not land on top of a scheduled run already going —
    the one overlap the cross-process lock exists to prevent."""
    from aida.config.paths import scheduler_lock_path
    from aida.core.proc_lock import try_acquire_scheduler_lock
    from aida.core.scheduler_runtime import fire_schedule_now

    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    )
    entry = settings.schedules.schedules["s"]

    finished = []
    with try_acquire_scheduler_lock(scheduler_lock_path()) as held:
        assert held is True
        await fire_schedule_now("s", entry, settings, on_run_finished=lambda *a: finished.append(a))

    assert finished[0][1] is False
    assert "already in progress" in finished[0][3]
