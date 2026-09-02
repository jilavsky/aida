"""Tests for aida.ui.qt.workflow_management_dialog — same config-CRUD-only,
no-bridge-needed pattern as test_workspace_management_dialog.py.
"""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    ProviderProfile,
    Settings,
    WorkflowConfig,
    WorkflowStep,
    WorkspaceConfig,
    WorkspacesConfig,
    list_workflow_names,
    load_settings,
    load_workflow,
    save_workflow,
)
from aida.ui.qt._qt import QDialog, QMessageBox
from aida.ui.qt.workflow_management_dialog import (
    NO_MCP_GROUP_LABEL,
    NO_PROFILE_LABEL,
    StepFormDialog,
    WorkflowFormDialog,
    WorkflowManagementDialog,
)


def _settings_with_workspace(aida_home: Path) -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    settings.workspaces = WorkspacesConfig(workspaces={"use-ws": WorkspaceConfig(name="use-ws")})
    return settings


# --- StepFormDialog -------------------------------------------------------


def test_step_form_defaults_when_adding(qapp):
    dialog = StepFormDialog()
    assert dialog._prompt_edit.toPlainText() == ""
    assert dialog._expect_files_edit.toPlainText() == ""


def test_step_form_seeds_fields_when_editing(qapp):
    step = WorkflowStep(prompt="Reduce the scans.", expect_files=["*.png", "*.csv"])
    dialog = StepFormDialog(step=step)
    assert dialog._prompt_edit.toPlainText() == "Reduce the scans."
    assert dialog._expect_files_edit.toPlainText() == "*.png\n*.csv"


def test_step_form_result_step_round_trips(qapp):
    dialog = StepFormDialog()
    dialog._prompt_edit.setPlainText("Plot it.")
    dialog._expect_files_edit.setPlainText("*.png\n*.pdf")
    step = dialog.result_step()
    assert step.prompt == "Plot it."
    assert step.expect_files == ["*.png", "*.pdf"]


def test_step_form_rejects_blank_prompt(qapp, monkeypatch):
    dialog = StepFormDialog()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


# --- WorkflowFormDialog: steps sub-editor ----------------------------------


def test_workflow_form_starts_with_seeded_steps(qapp, aida_home: Path):
    settings = _settings_with_workspace(aida_home)
    workflow = WorkflowConfig(
        name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="step one"), WorkflowStep(prompt="step two")]
    )
    dialog = WorkflowFormDialog(settings=settings, workflow=workflow)
    assert dialog._steps_list.count() == 2
    assert "step one" in dialog._steps_list.item(0).text()


def test_workflow_form_add_step_via_stub(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    dialog = WorkflowFormDialog(settings=settings)

    step_form = StepFormDialog()
    step_form._prompt_edit.setPlainText("new step")
    monkeypatch.setattr("aida.ui.qt.workflow_management_dialog.StepFormDialog", lambda **kw: step_form)
    monkeypatch.setattr(step_form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)

    dialog._on_add_step()

    assert dialog._steps_list.count() == 1
    assert dialog.result_config().steps[0].prompt == "new step"


def test_workflow_form_remove_step(aida_home: Path):
    settings = load_settings()
    dialog = WorkflowFormDialog(settings=settings, workflow=WorkflowConfig(name="w", steps=[WorkflowStep(prompt="a")]))
    dialog._steps_list.setCurrentRow(0)
    dialog._on_remove_step()
    assert dialog._steps_list.count() == 0


def test_workflow_form_move_step_up_and_down(aida_home: Path):
    settings = load_settings()
    workflow = WorkflowConfig(name="w", steps=[WorkflowStep(prompt="a"), WorkflowStep(prompt="b")])
    dialog = WorkflowFormDialog(settings=settings, workflow=workflow)

    dialog._steps_list.setCurrentRow(1)
    dialog._on_move_step_up()
    assert [s.prompt for s in dialog._steps] == ["b", "a"]

    dialog._on_move_step_down()
    assert [s.prompt for s in dialog._steps] == ["a", "b"]


# --- WorkflowFormDialog: top-level fields + validation ---------------------


def test_workflow_form_seeds_top_level_fields(qapp, aida_home: Path):
    settings = _settings_with_workspace(aida_home)
    workflow = WorkflowConfig(
        name="daily",
        description="a test workflow",
        workspace="use-ws",
        profile="mock-profile",
        vars={"folder": "/data"},
        preapproved_tools=["server__tool"],
        steps=[WorkflowStep(prompt="go")],
    )
    dialog = WorkflowFormDialog(settings=settings, workflow=workflow)

    assert dialog._name_edit.isReadOnly()
    assert dialog._name_edit.text() == "daily"
    assert dialog._description_edit.text() == "a test workflow"
    assert dialog._workspace_combo.currentText() == "use-ws"
    assert dialog._profile_combo.currentText() == "mock-profile"
    assert dialog._vars_edit.toPlainText() == "folder=/data"
    assert dialog._preapproved_edit.toPlainText() == "server__tool"


def test_workflow_form_is_edit_false_keeps_name_editable_even_with_a_draft(qapp, aida_home: Path):
    """The "Save Conversation as Workflow…" path: a pre-filled draft that
    is not yet a saved workflow must still have an editable name field."""
    settings = _settings_with_workspace(aida_home)
    draft = WorkflowConfig(name="", workspace="use-ws", steps=[WorkflowStep(prompt="hi")])
    dialog = WorkflowFormDialog(settings=settings, workflow=draft, is_edit=False)
    assert not dialog._name_edit.isReadOnly()


def test_workflow_form_none_profile_and_mcp_group_map_to_none(aida_home: Path):
    settings = load_settings()
    settings.workspaces = WorkspacesConfig(workspaces={"use-ws": WorkspaceConfig(name="use-ws")})
    dialog = WorkflowFormDialog(settings=settings)
    dialog._name_edit.setText("w")
    dialog._workspace_combo.setCurrentText("use-ws")
    dialog._steps = [WorkflowStep(prompt="go")]

    assert dialog._profile_combo.currentText() == NO_PROFILE_LABEL
    assert dialog._mcp_group_combo.currentText() == NO_MCP_GROUP_LABEL
    config = dialog.result_config()
    assert config.profile is None
    assert config.mcp_group is None


def test_workflow_form_rejects_blank_name(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.workspaces = WorkspacesConfig(workspaces={"use-ws": WorkspaceConfig(name="use-ws")})
    dialog = WorkflowFormDialog(settings=settings)
    dialog._steps = [WorkflowStep(prompt="go")]
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


def test_workflow_form_rejects_no_workspaces_configured(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()  # no workspaces at all
    dialog = WorkflowFormDialog(settings=settings)
    dialog._name_edit.setText("w")
    dialog._steps = [WorkflowStep(prompt="go")]
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


def test_workflow_form_rejects_no_steps(qapp, aida_home: Path, monkeypatch):
    settings = load_settings()
    settings.workspaces = WorkspacesConfig(workspaces={"use-ws": WorkspaceConfig(name="use-ws")})
    dialog = WorkflowFormDialog(settings=settings)
    dialog._name_edit.setText("w")
    dialog._workspace_combo.setCurrentText("use-ws")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


# --- WorkflowManagementDialog: list/detail ---------------------------------


def test_dialog_lists_stored_workflows(qapp, aida_home: Path):
    settings = _settings_with_workspace(aida_home)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    dialog = WorkflowManagementDialog(settings)
    assert dialog._workflow_list.count() == 1
    assert dialog._workflow_list.item(0).text() == "daily"


def test_dialog_with_no_workflows_is_empty(qapp, aida_home: Path):
    dialog = WorkflowManagementDialog(load_settings())
    assert dialog._workflow_list.count() == 0
    assert "no workflow selected" in dialog._details_label.text()


def test_detail_panel_shows_steps(qapp, aida_home: Path):
    settings = _settings_with_workspace(aida_home)
    save_workflow(
        WorkflowConfig(
            name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="reduce"), WorkflowStep(prompt="plot")]
        )
    )
    dialog = WorkflowManagementDialog(settings)
    dialog._workflow_list.setCurrentRow(0)
    assert "reduce" in dialog._details_label.text()
    assert "plot" in dialog._details_label.text()


# --- Add/Edit/Remove --------------------------------------------------------


def test_add_workflow_persists_to_disk(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    dialog = WorkflowManagementDialog(settings)

    form = WorkflowFormDialog(settings=settings)
    form._name_edit.setText("daily")
    form._workspace_combo.setCurrentText("use-ws")
    form._steps = [WorkflowStep(prompt="go")]
    monkeypatch.setattr("aida.ui.qt.workflow_management_dialog.WorkflowFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)

    dialog._on_add()

    assert "daily" in list_workflow_names()
    reloaded = load_workflow("daily")
    assert reloaded.workspace == "use-ws"
    assert reloaded.steps[0].prompt == "go"


def test_add_workflow_rejects_a_duplicate_name(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    dialog = WorkflowManagementDialog(settings)

    form = WorkflowFormDialog(settings=settings)
    form._name_edit.setText("daily")
    form._workspace_combo.setCurrentText("use-ws")
    form._steps = [WorkflowStep(prompt="go")]
    monkeypatch.setattr("aida.ui.qt.workflow_management_dialog.WorkflowFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_add()

    assert warned == [True]
    assert len(list_workflow_names()) == 1


def test_edit_workflow_persists_changes(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    dialog = WorkflowManagementDialog(settings)
    dialog._workflow_list.setCurrentRow(0)

    form = WorkflowFormDialog(settings=settings, workflow=load_workflow("daily"))
    form._description_edit.setText("updated")
    monkeypatch.setattr("aida.ui.qt.workflow_management_dialog.WorkflowFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)

    dialog._on_edit()

    assert load_workflow("daily").description == "updated"


def test_remove_workflow_deletes_the_file(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    dialog = WorkflowManagementDialog(settings)
    dialog._workflow_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    dialog._on_remove()

    assert "daily" not in list_workflow_names()


def test_remove_workflow_cancelled_keeps_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_workspace(aida_home)
    save_workflow(WorkflowConfig(name="daily", workspace="use-ws", steps=[WorkflowStep(prompt="go")]))
    dialog = WorkflowManagementDialog(settings)
    dialog._workflow_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    dialog._on_remove()

    assert "daily" in list_workflow_names()
