"""Tests for aida.ui.qt.scheduler_bridge.SchedulerBridge — the Qt adapter
over aida.core.scheduler_runtime.scheduler_loop.
"""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    ProviderProfile,
    ScheduleEntry,
    SchedulesConfig,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_settings,
    save_schedules_config,
    save_workflow,
)
from aida.providers.mock import MockProvider, MockTurn
from aida.ui.qt.scheduler_bridge import SchedulerBridge
from tests.ui._qt_test_utils import pump_until


def _settings_with_due_schedule():
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(
        workspaces={"use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")}
    )
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    schedules = SchedulesConfig(schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")})
    save_schedules_config(schedules)
    settings.schedules = schedules
    return settings


def test_start_runs_a_due_schedule_and_emits_signals(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")]))
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    started = []
    finished = []
    scheduler.run_started.connect(started.append)
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    scheduler.start()
    assert pump_until(qapp, lambda: finished), "run_finished never fired"

    assert started == ["s"]
    assert finished[0][0] == "s"
    assert finished[0][1] is True  # ok
    assert finished[0][2]  # conversation_id

    scheduler.stop()


def test_start_is_idempotent(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")] * 3))
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    finished = []
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    scheduler.start()
    scheduler.start()  # second call must be a no-op, not a second concurrent loop
    assert pump_until(qapp, lambda: finished), "run_finished never fired"

    scheduler.stop()


def test_stop_before_start_is_a_safe_noop(qapp, loop_thread):
    scheduler = SchedulerBridge(loop_thread)
    scheduler.stop()  # must not raise


def test_stop_ends_the_loop(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """After stop(), the loop must not still be running (a fresh start()
    schedules a fresh coroutine — see the _future reset in stop())."""
    settings = load_settings()  # no schedules configured — the loop just ticks and sleeps
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", lambda: settings)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    scheduler.start()
    qapp.processEvents()
    scheduler.stop()

    assert scheduler._future is None


def test_run_now_fires_regardless_of_due_state(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    """run_now must fire even when the schedule isn't due by the clock —
    same "forced run" contract as `aida schedule run NAME`."""
    from aida.config.settings import save_providers_config, save_workspaces_config

    settings = _settings_with_due_schedule()  # "every 1h", never fired => already due anyway
    save_providers_config(settings.providers)
    save_workspaces_config(settings.workspaces)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")] * 5))

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    finished = []
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    entry = settings.schedules.schedules["s"]
    scheduler.run_now("s", entry, settings)
    assert pump_until(qapp, lambda: finished), "run_finished never fired"
    assert finished[0][0] == "s"
    assert finished[0][1] is True

    finished.clear()
    scheduler.run_now("s", entry, settings)  # forced again, right away — must still fire
    assert pump_until(qapp, lambda: finished), "second forced run never fired"
    assert finished[0][1] is True
