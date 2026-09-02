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
        workspaces={"use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")}
    )
    if schedules is not None:
        settings.schedules = schedules
    return settings


def _workflow(name: str = "daily") -> None:
    save_workflow(WorkflowConfig(name=name, workspace="use-ws", steps=[WorkflowStep(prompt="go")]))


@pytest.mark.asyncio
async def test_disabled_schedule_never_runs(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1m", enabled=False)})
    )

    ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []


@pytest.mark.asyncio
async def test_never_fired_every_schedule_runs_immediately(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}))

    finished = []
    ran = await run_due_schedules(
        settings, now=datetime(2026, 9, 2, 10, 0), on_run_finished=lambda *args: finished.append(args)
    )

    assert ran == ["s"]
    assert finished == [("s", True, finished[0][2], None)]
    assert finished[0][2] is not None  # conversation_id


@pytest.mark.asyncio
async def test_not_due_schedule_is_skipped(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="4h")}))
    now = datetime(2026, 9, 2, 10, 0)

    first = await run_due_schedules(settings, now=now)
    assert first == ["s"]

    second = await run_due_schedules(settings, now=now + timedelta(hours=1))  # only 1h later
    assert second == []


@pytest.mark.asyncio
async def test_catch_up_fires_once_not_repeatedly(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")] * 5))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="4h")}))
    now = datetime(2026, 9, 2, 10, 0)

    await run_due_schedules(settings, now=now)  # fires (never fired before)
    ran_again = await run_due_schedules(settings, now=now + timedelta(days=3))  # a big gap
    assert ran_again == ["s"]  # fires exactly once for the whole gap
    ran_immediately_after = await run_due_schedules(settings, now=now + timedelta(days=3))
    assert ran_immediately_after == []  # not fired twice for the same gap


@pytest.mark.asyncio
async def test_malformed_at_every_is_skipped_without_crashing(monkeypatch, aida_home: Path, records_home: Path):
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily")}))  # neither set

    ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []


@pytest.mark.asyncio
async def test_missing_workflow_records_config_error_and_calls_on_run_finished(aida_home: Path, records_home: Path):
    settings = _settings(
        SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="does-not-exist", every="1h")})
    )

    finished = []
    ran = await run_due_schedules(
        settings, now=datetime(2026, 9, 2, 10, 0), on_run_finished=lambda *args: finished.append(args)
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
async def test_workflow_agent_error_records_failed_status(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(error="boom")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}))

    finished = []
    await run_due_schedules(
        settings, now=datetime(2026, 9, 2, 10, 0), on_run_finished=lambda *args: finished.append(args)
    )

    assert finished[0][1] is False
    assert "boom" in finished[0][3]
    store = ScheduleRunStore()
    last = store.last_run("s")
    store.close()
    assert last.status == "failed"


@pytest.mark.asyncio
async def test_on_run_started_called_before_on_run_finished(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}))

    events = []
    await run_due_schedules(
        settings,
        now=datetime(2026, 9, 2, 10, 0),
        on_run_started=lambda name: events.append(("started", name)),
        on_run_finished=lambda *args: events.append(("finished", args[0])),
    )

    assert events == [("started", "s"), ("finished", "s")]


@pytest.mark.asyncio
async def test_tick_skipped_entirely_when_lock_held_elsewhere(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}))

    from aida.config.paths import scheduler_lock_path

    with try_acquire_scheduler_lock(scheduler_lock_path()) as held:
        assert held is True
        ran = await run_due_schedules(settings, now=datetime(2026, 9, 2, 10, 0))

    assert ran == []  # the whole tick was skipped, not partially run


@pytest.mark.asyncio
async def test_scheduler_loop_runs_a_tick_and_stops_on_stop_event(monkeypatch, aida_home: Path, records_home: Path):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    _workflow()
    settings = _settings(SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}))
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", lambda: settings)

    stop_event = asyncio.Event()
    finished = []

    def _on_finished(*args):
        finished.append(args)
        stop_event.set()

    await asyncio.wait_for(
        scheduler_loop(poll_interval_seconds=60, on_run_finished=_on_finished, stop_event=stop_event),
        timeout=5.0,
    )

    assert len(finished) == 1
    assert finished[0][0] == "s"
