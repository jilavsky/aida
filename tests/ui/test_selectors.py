"""Tests for aida.ui.qt.selectors — workspace/profile dropdowns, folder
display, MCP quick panel."""

from __future__ import annotations

from aida.ui.qt.selectors import (
    NO_WORKSPACE_LABEL,
    FolderDisplay,
    McpQuickPanel,
    ProfileSelector,
    WorkspaceSelector,
)


def test_workspace_selector_includes_no_workspace_option(qapp):
    selector = WorkspaceSelector()
    selector.set_workspaces(["use-pyirena", "plain-chat"])
    assert selector._combo.itemText(0) == NO_WORKSPACE_LABEL
    assert selector.current_workspace() == ""  # defaults to "(no workspace)"


def test_workspace_selector_set_current_selects_it(qapp):
    selector = WorkspaceSelector()
    selector.set_workspaces(["use-pyirena", "plain-chat"], current="plain-chat")
    assert selector.current_workspace() == "plain-chat"


def test_workspace_selector_emits_signal_on_change(qapp):
    selector = WorkspaceSelector()
    selector.set_workspaces(["use-pyirena", "plain-chat"])
    changes = []
    selector.workspace_changed.connect(changes.append)

    selector._combo.setCurrentText("use-pyirena")
    assert changes == ["use-pyirena"]

    selector._combo.setCurrentText(NO_WORKSPACE_LABEL)
    assert changes == ["use-pyirena", ""]


def test_profile_selector_set_and_get_current(qapp):
    selector = ProfileSelector()
    selector.set_profiles(["argo-claude", "local-ollama"], current="local-ollama")
    assert selector.current_profile() == "local-ollama"


def test_profile_selector_emits_on_change(qapp):
    selector = ProfileSelector()
    selector.set_profiles(["a", "b"])
    changes = []
    selector.profile_changed.connect(changes.append)
    selector._combo.setCurrentText("b")
    assert changes == ["b"]


def test_folder_display_shows_none_by_default(qapp):
    display = FolderDisplay()
    assert "(none)" in display._source_label.text()
    assert "(none)" in display._target_label.text()


def test_folder_display_set_folders_updates_labels(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=["/data/a", "/data/b"], target_folder="/out")
    assert "/data/a" in display._source_label.text()
    assert "/data/b" in display._source_label.text()
    assert "/out" in display._target_label.text()
    assert display.source_folders == ["/data/a", "/data/b"]
    assert display.target_folder == "/out"


def test_folder_display_add_source_folder_via_dialog(qapp, monkeypatch, tmp_path):
    display = FolderDisplay()
    monkeypatch.setattr(
        "aida.ui.qt.selectors.QFileDialog.getExistingDirectory", lambda *a, **kw: str(tmp_path)
    )
    changed = []
    display.source_folders_changed.connect(changed.append)

    display._on_add_source_folder()

    assert display.source_folders == [str(tmp_path)]
    assert changed == [[str(tmp_path)]]


def test_folder_display_dialog_cancelled_does_nothing(qapp, monkeypatch):
    display = FolderDisplay()
    monkeypatch.setattr("aida.ui.qt.selectors.QFileDialog.getExistingDirectory", lambda *a, **kw: "")
    changed = []
    display.source_folders_changed.connect(changed.append)

    display._on_add_source_folder()

    assert display.source_folders == []
    assert changed == []


def test_folder_display_save_to_workspace_emits_signal(qapp):
    display = FolderDisplay()
    requested = []
    display.save_to_workspace_requested.connect(lambda: requested.append(True))
    display._save_button.click()
    assert requested == [True]


def test_mcp_quick_panel_shows_group_and_checkboxes(qapp):
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="pyirena-analysis")
    assert "pyirena-analysis" in panel._group_label.text()
    assert panel.enabled_servers() == ["pyirena"]


def test_mcp_quick_panel_toggle_updates_enabled_servers(qapp):
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="analysis")

    changes = []
    panel.enabled_servers_changed.connect(changes.append)
    panel._checkboxes["bait"].setChecked(True)

    assert set(changes[-1]) == {"pyirena", "bait"}
    assert set(panel.enabled_servers()) == {"pyirena", "bait"}


def test_mcp_quick_panel_reset_servers_clears_old_checkboxes(qapp):
    panel = McpQuickPanel()
    panel.set_servers(["pyirena"], enabled=["pyirena"], group_name="a")
    panel.set_servers(["other"], enabled=[], group_name="b")
    assert list(panel._checkboxes.keys()) == ["other"]
