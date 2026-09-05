"""Tests for aida.ui.qt.schedule_management_dialog — same config-CRUD-only,
no-bridge-needed pattern as test_workspace_management_dialog.py's Add/
Edit/Remove tests, plus Run Now tests that do need a SchedulerBridge (a
real one, on a real loop_thread, same as test_scheduler_bridge.py).
"""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    ProviderProfile,
    ScheduleEntry,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    load_schedules_config,
    load_settings,
    save_workflow,
)
from aida.providers.mock import MockProvider, MockTurn
from aida.ui.qt._qt import QDialog, QMessageBox
from aida.ui.qt.schedule_management_dialog import (
    AT_LABEL,
    EVERY_LABEL,
    ScheduleFormDialog,
    ScheduleManagementDialog,
)
from aida.ui.qt.scheduler_bridge import SchedulerBridge
from tests.ui._qt_test_utils import pump_until


def _settings_with_workflow(aida_home: Path):
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
    return settings


# --- ScheduleFormDialog -------------------------------------------------


def test_form_defaults_when_adding(qapp, aida_home: Path):
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    assert not dialog._name_edit.isReadOnly()
    assert dialog._name_edit.text() == ""
    assert dialog._timing_kind_combo.currentText() == AT_LABEL
    assert dialog._enabled_checkbox.isChecked()


def test_form_seeds_fields_when_editing_an_at_schedule(qapp, aida_home: Path):
    entry = ScheduleEntry(name="nightly", workflow="daily", at="07:00", enabled=False)
    dialog = ScheduleFormDialog(workflow_names=["daily"], entry=entry)

    assert dialog._name_edit.isReadOnly()
    assert dialog._name_edit.text() == "nightly"
    assert dialog._workflow_combo.currentText() == "daily"
    assert dialog._timing_kind_combo.currentText() == AT_LABEL
    assert dialog._timing_value_edit.text() == "07:00"
    assert not dialog._enabled_checkbox.isChecked()


def test_form_seeds_fields_when_editing_an_every_schedule(qapp, aida_home: Path):
    entry = ScheduleEntry(name="often", workflow="daily", every="4h")
    dialog = ScheduleFormDialog(workflow_names=["daily"], entry=entry)

    assert dialog._timing_kind_combo.currentText() == EVERY_LABEL
    assert dialog._timing_value_edit.text() == "4h"


def test_form_result_entry_reflects_at_timing():
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    dialog._name_edit.setText("nightly")
    dialog._workflow_combo.setCurrentText("daily")
    dialog._timing_kind_combo.setCurrentText(AT_LABEL)
    dialog._timing_value_edit.setText("07:00")

    entry = dialog.result_entry()
    assert entry.at == "07:00"
    assert entry.every is None


def test_form_result_entry_reflects_every_timing():
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    dialog._name_edit.setText("often")
    dialog._workflow_combo.setCurrentText("daily")
    dialog._timing_kind_combo.setCurrentText(EVERY_LABEL)
    dialog._timing_value_edit.setText("4h")

    entry = dialog.result_entry()
    assert entry.every == "4h"
    assert entry.at is None


def test_form_parses_vars_and_preapproved_tools():
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    dialog._name_edit.setText("nightly")
    dialog._workflow_combo.setCurrentText("daily")
    dialog._timing_value_edit.setText("07:00")
    dialog._vars_edit.setPlainText("folder=/data/today\nrg_min=20")
    dialog._preapproved_edit.setPlainText("pyirena__reduce_scan\nother__tool")

    entry = dialog.result_entry()
    assert entry.vars == {"folder": "/data/today", "rg_min": "20"}
    assert entry.preapproved_tools == ["pyirena__reduce_scan", "other__tool"]


def test_form_rejects_blank_name(qapp, monkeypatch):
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    dialog._timing_value_edit.setText("07:00")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_accept()

    assert warned == [True]


def test_form_rejects_invalid_timing(qapp, monkeypatch):
    dialog = ScheduleFormDialog(workflow_names=["daily"])
    dialog._name_edit.setText("nightly")
    dialog._workflow_combo.setCurrentText("daily")
    dialog._timing_value_edit.setText("not-a-time")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_accept()

    assert warned == [True]


# --- ScheduleManagementDialog: list/detail --------------------------------


def test_dialog_lists_configured_schedules(qapp, aida_home: Path):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00"
    )
    dialog = ScheduleManagementDialog(settings, None)

    assert dialog._schedule_list.count() == 1
    assert "nightly" in dialog._schedule_list.item(0).text()
    assert "enabled" in dialog._schedule_list.item(0).text()


def test_dialog_with_no_schedules_is_empty(qapp, aida_home: Path):
    dialog = ScheduleManagementDialog(load_settings(), None)
    assert dialog._schedule_list.count() == 0
    assert "no schedule selected" in dialog._details_label.text()


def test_detail_panel_shows_last_run_never(qapp, aida_home: Path):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00"
    )
    dialog = ScheduleManagementDialog(settings, None)
    dialog._schedule_list.setCurrentRow(0)

    assert "never run" in dialog._details_label.text()


# --- Add/Edit/Enable/Disable/Remove ---------------------------------------


def test_add_schedule_persists_to_settings_and_disk(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workflow(aida_home)
    dialog = ScheduleManagementDialog(settings, None)

    form = ScheduleFormDialog(workflow_names=["daily"])
    form._name_edit.setText("nightly")
    form._workflow_combo.setCurrentText("daily")
    form._timing_value_edit.setText("07:00")
    monkeypatch.setattr(
        "aida.ui.qt.schedule_management_dialog.ScheduleFormDialog", lambda **kw: form
    )
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)

    dialog._on_add()

    assert "nightly" in settings.schedules.schedules
    reloaded = load_schedules_config(aida_home)
    assert reloaded.schedules["nightly"].workflow == "daily"
    assert reloaded.schedules["nightly"].at == "07:00"


def test_add_schedule_rejects_a_duplicate_name(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00"
    )
    dialog = ScheduleManagementDialog(settings, None)

    form = ScheduleFormDialog(workflow_names=["daily"])
    form._name_edit.setText("nightly")
    monkeypatch.setattr(
        "aida.ui.qt.schedule_management_dialog.ScheduleFormDialog", lambda **kw: form
    )
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_add()

    assert warned == [True]
    assert len(settings.schedules.schedules) == 1


def test_enable_disable_roundtrip(qapp, aida_home: Path):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00", enabled=True
    )
    dialog = ScheduleManagementDialog(settings, None)
    dialog._schedule_list.setCurrentRow(0)

    dialog._on_disable()
    assert settings.schedules.schedules["nightly"].enabled is False
    assert load_schedules_config(aida_home).schedules["nightly"].enabled is False

    dialog._on_enable()
    assert settings.schedules.schedules["nightly"].enabled is True


def test_remove_schedule_deletes_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00"
    )
    dialog = ScheduleManagementDialog(settings, None)
    dialog._schedule_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    dialog._on_remove()

    assert "nightly" not in settings.schedules.schedules
    assert dialog._schedule_list.count() == 0


def test_remove_schedule_cancelled_keeps_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", at="07:00"
    )
    dialog = ScheduleManagementDialog(settings, None)
    dialog._schedule_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    dialog._on_remove()

    assert "nightly" in settings.schedules.schedules


# --- Run Now (needs a real SchedulerBridge) -------------------------------


def test_run_now_with_no_bridge_warns_instead_of_crashing(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", every="1h"
    )
    dialog = ScheduleManagementDialog(settings, None)
    dialog._schedule_list.setCurrentRow(0)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_run_now()

    assert warned == [True]


def test_run_now_fires_via_scheduler_bridge_and_refreshes_last_run(
    qapp, loop_thread, aida_home: Path, records_home: Path, monkeypatch
):
    monkeypatch.setattr(
        "aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="done")])
    )
    settings = _settings_with_workflow(aida_home)
    settings.schedules.schedules["nightly"] = ScheduleEntry(
        name="nightly", workflow="daily", every="1h"
    )

    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    dialog = ScheduleManagementDialog(settings, scheduler)
    dialog._schedule_list.setCurrentRow(0)

    dialog._on_run_now()
    assert pump_until(
        qapp,
        lambda: (
            "nightly" not in dialog._running_names
            and "never run" not in dialog._details_label.text()
        ),
    )

    assert "ok at" in dialog._details_label.text()
    scheduler.stop()


def test_dialog_disconnects_from_scheduler_bridge_on_close(qapp, loop_thread, aida_home: Path):
    """Regression guard for the exact leaked-instance bug class
    McpManagementDialog.done's docstring describes: a closed dialog must
    stop reacting to a still-live SchedulerBridge's signals."""
    settings = _settings_with_workflow(aida_home)
    scheduler = SchedulerBridge(loop_thread, poll_interval_seconds=60)
    dialog = ScheduleManagementDialog(settings, scheduler)

    dialog.done(QDialog.DialogCode.Accepted)

    # After done(), the dialog's slots must no longer be connected —
    # emitting must not raise even though the dialog is "closed".
    scheduler.run_started.emit("whatever")
    scheduler.run_finished.emit("whatever", True, "conv", "")
    scheduler.stop()
