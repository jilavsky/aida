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
        workspaces={
            "use-ws": WorkspaceConfig(name="use-ws", profile="mock-profile", safety="relaxed")
        }
    )
    save_workflow(
        WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")])
    )
    schedules = SchedulesConfig(
        schedules={"s": ScheduleEntry(name="s", workflow="daily", every="1h")}
    )
    save_schedules_config(schedules)
    settings.schedules = schedules
    return settings


def test_start_runs_a_due_schedule_and_emits_signals(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    # defer_to_user=False: this covers the loop/signal mechanics, not the
    # quiet-period policy (which has its own tests below and in
    # tests/test_scheduler_runtime.py). Left on, the default 5-minute quiet
    # period would hold every run back and this would test nothing.
    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60, defer_to_user=False)
    started = []
    finished = []
    scheduler.run_started.connect(started.append)
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    # try/finally, not a trailing stop() call: CI flake (Windows, Python
    # 3.11) — an assertion failing between start() and stop() used to skip
    # stop() entirely, leaving the background loop's task alive into this
    # test's own fixture teardown. aida_home/records_home's AIDA_HOME
    # override gets reverted there (monkeypatch's finalizer), so a tick
    # that finally got to run afterwards did its file lookups against the
    # *real* ~/.aida on the CI runner instead of the test's tmp_path — that
    # is exactly the "no workflow named 'daily'... Available: (none)"
    # warning that showed up in this test's "teardown" capture, not its
    # call capture, on the run that failed. stop() blocks until the loop
    # thread's task actually returns, so it must run even when an assert
    # below raises.
    try:
        scheduler.start()
        assert pump_until(qapp, lambda: finished), "run_finished never fired"

        assert started == ["s"]
        assert finished[0][0] == "s"
        assert finished[0][1] is True  # ok
        assert finished[0][2]  # conversation_id
    finally:
        scheduler.stop()


def test_start_is_idempotent(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="done")] * 3),
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60, defer_to_user=False)
    finished = []
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    try:
        scheduler.start()
        scheduler.start()  # second call must be a no-op, not a second concurrent loop
        assert pump_until(qapp, lambda: finished), "run_finished never fired"
    finally:
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
    try:
        scheduler.start()
        qapp.processEvents()
    finally:
        scheduler.stop()

    assert scheduler._future is None


def test_run_now_fires_regardless_of_due_state(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """run_now must fire even when the schedule isn't due by the clock —
    same "forced run" contract as `aida schedule run NAME`."""
    from aida.config.settings import save_providers_config, save_workspaces_config

    settings = _settings_with_due_schedule()  # "every 1h", never fired => already due anyway
    save_providers_config(settings.providers)
    save_workspaces_config(settings.workspaces)
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="done")] * 5),
    )

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


# --- deferring to the user -------------------------------------------------


def test_due_job_is_deferred_while_the_user_is_active(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The default: a job that comes due right after the user did anything
    waits out the quiet period instead of firing on top of them."""
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    scheduler.activity.note_activity()  # "the user just did something"
    deferrals = []
    finished = []
    scheduler.deferred_changed.connect(deferrals.append)
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    try:
        scheduler.start()
        assert pump_until(qapp, lambda: deferrals), "deferred_changed never fired"

        assert "s" in deferrals[0]
        assert "waiting" in deferrals[0]["s"]
        assert finished == []  # and it really did not run
    finally:
        scheduler.stop()


def test_a_running_turn_defers_hard(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    scheduler.activity.turn_in_flight = True
    # Idle for hours as far as the quiet period is concerned — only the
    # live turn should be holding it back.
    scheduler.activity.last_activity_monotonic -= 10_000
    deferrals = []
    scheduler.deferred_changed.connect(deferrals.append)

    try:
        scheduler.start()
        assert pump_until(qapp, lambda: deferrals), "deferred_changed never fired"

        assert deferrals[0]["s"] == "a turn is running"
    finally:
        scheduler.stop()


def test_job_runs_once_the_quiet_period_has_elapsed(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    scheduler.activity.last_activity_monotonic -= 10_000  # long since idle
    finished = []
    scheduler.run_finished.connect(lambda *args: finished.append(args))

    try:
        scheduler.start()
        assert pump_until(qapp, lambda: finished), "run_finished never fired"
        assert finished[0][1] is True
    finally:
        scheduler.stop()


def test_unsent_text_defers(qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", _settings_with_due_schedule)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    scheduler.activity.last_activity_monotonic -= 10_000  # quiet period satisfied
    scheduler.activity.has_unsent_text = True
    deferrals = []
    scheduler.deferred_changed.connect(deferrals.append)

    try:
        scheduler.start()
        assert pump_until(qapp, lambda: deferrals), "deferred_changed never fired"
        assert "unsent text" in deferrals[0]["s"]
    finally:
        scheduler.stop()


def test_deferred_changed_reports_an_empty_snapshot_when_nothing_waits(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    """The snapshot is authoritative every tick, so "nothing waiting" has
    to be reported too — that is what lets a UI badge clear itself."""
    settings = load_settings()  # no schedules at all
    monkeypatch.setattr("aida.core.scheduler_runtime.load_settings", lambda: settings)

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    snapshots = []
    scheduler.deferred_changed.connect(snapshots.append)

    try:
        scheduler.start()
        assert pump_until(qapp, lambda: snapshots), "deferred_changed never fired"
        assert snapshots[0] == {}
    finally:
        scheduler.stop()
