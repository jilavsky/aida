"""Tests for aida.ui.qt.workspace_management_dialog (U1) — same
config-CRUD-only, no-bridge-needed pattern as
tests/ui/test_mcp_management_dialog.py's Add/Edit/Remove tests."""

from __future__ import annotations

from pathlib import Path

from aida.config.settings import (
    KnowledgeBaseConfig,
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    load_settings,
    load_workspaces_config,
)
from aida.ui.qt._qt import QDialog, QMessageBox
from aida.ui.qt.workspace_management_dialog import WorkspaceFormDialog, WorkspaceManagementDialog
from aida.workspace.workspaces import WorkspaceConfig


def _settings_with_a_profile(aida_home: Path):
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(name="mock-profile", kind="openai_compat", model="mock-model")
    return settings


# --- WorkspaceFormDialog ------------------------------------------------------


def test_workspace_form_seeds_fields_when_editing(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    workspace = WorkspaceConfig(
        name="pyirena",
        profile="mock-profile",
        source_folders=["/data/in"],
        target_folder="/data/out",
        sidecar_folder_name="figures",
        mcp_group="analysis",
        system_prompt="You are a SAXS expert.",
        safety="confirm",
        scripting_enabled=False,
        python_interpreter="/usr/bin/python3",
        command_allowlist=["git status"],
        script_timeout_seconds=180.0,
    )
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills", workspace=workspace)

    assert dialog._name_edit.isReadOnly()
    assert dialog._name_edit.text() == "pyirena"
    assert dialog._profile_combo.currentText() == "mock-profile"
    assert dialog._source_folders_edit.toPlainText() == "/data/in"
    assert dialog._target_folder_edit.text() == "/data/out"
    assert dialog._mcp_group_combo.currentText() == "analysis"
    assert dialog._system_prompt_edit.toPlainText() == "You are a SAXS expert."
    assert not dialog._scripting_checkbox.isChecked()
    assert dialog._interpreter_edit.text() == "/usr/bin/python3"
    assert dialog._command_allowlist_edit.toPlainText() == "git status"
    assert dialog._script_timeout_spin.value() == 180


def test_workspace_form_defaults_when_adding(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    assert dialog._profile_combo.currentText() == "(none)"
    assert dialog._mcp_group_combo.currentText() == "none"
    assert dialog._safety_combo.currentText() == "confirm"
    assert dialog._scripting_checkbox.isChecked()
    assert dialog._script_timeout_spin.value() == 30


def test_workspace_form_script_timeout_round_trips_into_result_config(qapp, aida_home: Path):
    """B5: the GUI's Script/command timeout field is the last thing this
    dialog reconstructs into a WorkspaceConfig — before this, the field
    didn't exist at all, so an edited timeout would have silently been
    lost (result_config() rebuilds a fresh WorkspaceConfig from just the
    form widgets, same footgun that already applies to templates_dir/
    saved_scripts_dir, which this dialog still doesn't expose)."""
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    dialog._name_edit.setText("ws")
    dialog._script_timeout_spin.setValue(600)

    config = dialog.result_config()
    assert config.script_timeout_seconds == 600.0


def test_workspace_form_result_config_reflects_edited_fields(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    dialog._name_edit.setText("pyirena")
    dialog._profile_combo.setCurrentText("mock-profile")
    dialog._source_folders_edit.setPlainText("/data/one\n/data/two")
    dialog._target_folder_edit.setText("/data/out")
    dialog._command_allowlist_edit.setPlainText("git status\ngit log *")

    config = dialog.result_config()
    assert config.name == "pyirena"
    assert config.profile == "mock-profile"
    assert config.source_folders == ["/data/one", "/data/two"]
    assert config.target_folder == "/data/out"
    assert config.command_allowlist == ["git status", "git log *"]


def test_workspace_form_none_profile_selection_maps_to_none(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    dialog._name_edit.setText("ws")
    assert dialog._profile_combo.currentText() == "(none)"
    assert dialog.result_config().profile is None


def test_workspace_form_lists_configured_skills_and_knowledge_bases(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    settings.knowledge.knowledge_bases["papers"] = KnowledgeBaseConfig(name="papers")
    skills_dir = aida_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "saxs-basics.md").write_text("SAXS basics.", encoding="utf-8")

    dialog = WorkspaceFormDialog(settings=settings, skills_dir=skills_dir)

    skill_names = [dialog._skills_list.item(i).text() for i in range(dialog._skills_list.count())]
    assert "saxs-basics" in skill_names
    kb_names = [dialog._kb_list.item(i).text() for i in range(dialog._kb_list.count())]
    assert "papers" in kb_names


def test_workspace_form_checked_skills_and_kbs_round_trip(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    settings.knowledge.knowledge_bases["papers"] = KnowledgeBaseConfig(name="papers")
    skills_dir = aida_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "saxs-basics.md").write_text("SAXS basics.", encoding="utf-8")

    workspace = WorkspaceConfig(name="pyirena", skills=["saxs-basics"], knowledge_bases=["papers"])
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=skills_dir, workspace=workspace)

    config = dialog.result_config()
    assert config.skills == ["saxs-basics"]
    assert config.knowledge_bases == ["papers"]


def test_workspace_form_shows_mcp_groups_from_settings(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    settings.mcp = McpConfig(
        servers={"pyirena-mcp": McpServerConfig(name="pyirena-mcp", command="/opt/x", groups=["analysis"])}
    )
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    group_names = [dialog._mcp_group_combo.itemText(i) for i in range(dialog._mcp_group_combo.count())]
    assert "analysis" in group_names


def test_workspace_form_rejects_a_blank_name(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()
    assert warned == [True]


def test_workspace_form_warns_when_relaxed_mode_is_newly_enabled(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    dialog._name_edit.setText("ws")
    dialog._safety_combo.setCurrentText("relaxed")

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: (warned.append(True), QMessageBox.StandardButton.Cancel)[1])
    dialog._on_accept()

    assert warned == [True]
    assert dialog.result() != QDialog.DialogCode.Accepted  # Cancel on the warning blocks the save


def test_workspace_form_does_not_warn_when_already_relaxed(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    workspace = WorkspaceConfig(name="ws", safety="relaxed")
    dialog = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills", workspace=workspace)

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))
    dialog._on_accept()

    assert warned == []
    assert dialog.result() == QDialog.DialogCode.Accepted


# --- WorkspaceManagementDialog: config CRUD ----------------------------------


def test_dialog_lists_configured_workspaces(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena")
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")
    assert dialog._workspace_list.count() == 1
    assert dialog._workspace_list.item(0).text() == "pyirena"


def test_dialog_with_no_workspaces_is_empty(qapp, aida_home: Path):
    dialog = WorkspaceManagementDialog(load_settings(), aida_home / "skills")
    assert dialog._workspace_list.count() == 0
    assert "no workspace selected" in dialog._details_label.text()


def test_add_workspace_persists_to_settings_and_disk(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")

    form = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    form._name_edit.setText("pyirena")
    form._profile_combo.setCurrentText("mock-profile")
    form._target_folder_edit.setText("/data/out")
    monkeypatch.setattr("aida.ui.qt.workspace_management_dialog.WorkspaceFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)

    dialog._on_add()

    assert "pyirena" in settings.workspaces.workspaces
    reloaded = load_workspaces_config(aida_home)
    assert "pyirena" in reloaded.workspaces
    assert reloaded.workspaces["pyirena"].target_folder == "/data/out"


def test_add_workspace_rejects_a_duplicate_name(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena")
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")

    form = WorkspaceFormDialog(settings=settings, skills_dir=aida_home / "skills")
    form._name_edit.setText("pyirena")
    monkeypatch.setattr("aida.ui.qt.workspace_management_dialog.WorkspaceFormDialog", lambda **kw: form)
    monkeypatch.setattr(form.__class__, "exec", lambda self: QDialog.DialogCode.Accepted)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(True))

    dialog._on_add()

    assert warned == [True]
    assert len(settings.workspaces.workspaces) == 1


def test_remove_workspace_deletes_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena")
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")
    dialog._workspace_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    dialog._on_remove()

    assert "pyirena" not in settings.workspaces.workspaces
    assert dialog._workspace_list.count() == 0


def test_remove_workspace_cancelled_keeps_it(qapp, aida_home: Path, monkeypatch):
    settings = _settings_with_a_profile(aida_home)
    settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena")
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")
    dialog._workspace_list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    dialog._on_remove()

    assert "pyirena" in settings.workspaces.workspaces


def test_detail_panel_shows_validation_warnings(qapp, aida_home: Path):
    settings = _settings_with_a_profile(aida_home)
    settings.workspaces.workspaces["pyirena"] = WorkspaceConfig(name="pyirena", source_folders=["/no/such/folder"])
    dialog = WorkspaceManagementDialog(settings, aida_home / "skills")
    dialog._workspace_list.setCurrentRow(0)
    assert "source folder not currently reachable" in dialog._details_label.text()
