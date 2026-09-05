"""Tests for aida.ui.qt.selectors — workspace/profile dropdowns, folder
display, MCP quick panel."""

from __future__ import annotations

from aida.ui.qt._qt import Qt
from aida.ui.qt.selectors import (
    NO_WORKSPACE_LABEL,
    FolderDisplay,
    McpQuickPanel,
    ProfileSelector,
    UserSelector,
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


def test_user_selector_is_editable_and_has_a_blank_first_entry(qapp):
    selector = UserSelector()
    selector.set_users(["Alice", "Bob"])
    assert selector._combo.isEditable()
    assert selector._combo.itemText(0) == ""
    assert selector.current_user() == ""


def test_user_selector_sets_known_or_new_current_user(qapp):
    selector = UserSelector()
    selector.set_users(["Alice", "Bob"], current="Bob")
    assert selector.current_user() == "Bob"

    selector.set_users(["Alice", "Bob"], current="New project")
    assert selector.current_user() == "New project"


def test_user_selector_emits_blank_and_named_changes(qapp):
    selector = UserSelector()
    selector.set_users(["Alice"], current="Alice")
    changes = []
    selector.user_changed.connect(changes.append)

    selector._combo.setCurrentText("")
    selector._combo.lineEdit().editingFinished.emit()
    selector._combo.setCurrentText("New project")
    selector._combo.lineEdit().editingFinished.emit()

    assert changes == ["", "New project"]


def test_user_selector_does_not_emit_while_a_name_is_being_typed(qapp):
    """The bug this guards: with ``currentTextChanged`` wired up, typing
    "jan" emitted "j", "ja", "jan" — and since the handler restarts the
    session, the app tore down and rebuilt a session three times while the
    user was still on the first letter. It passed every test that used
    ``setCurrentText``, because that sets the whole string at once."""
    selector = UserSelector()
    selector.set_users(["Alice"])
    changes = []
    selector.user_changed.connect(changes.append)

    line = selector._combo.lineEdit()
    for partial in ("j", "ja", "jan"):
        line.setText(partial)
        line.textEdited.emit(partial)
    assert changes == [], "typing must not commit anything"

    line.editingFinished.emit()
    assert changes == ["jan"], "committing (Return / focus out) is what emits"


def test_user_selector_ignores_a_commit_that_changes_nothing(qapp):
    """Return on an unchanged box, or focus merely passing through, must
    not restart the session."""
    selector = UserSelector()
    selector.set_users(["Alice"], current="Alice")
    changes = []
    selector.user_changed.connect(changes.append)

    selector._combo.lineEdit().editingFinished.emit()
    selector._combo.lineEdit().editingFinished.emit()
    assert changes == []


def test_user_selector_repopulating_is_not_a_user_action(qapp):
    """`set_users` runs on every sidebar refresh; it must never look like
    somebody picked a name."""
    selector = UserSelector()
    changes = []
    selector.user_changed.connect(changes.append)

    selector.set_users(["Alice", "Bob"], current="Bob")
    selector.set_users(["Alice", "Bob", "Carol"], current="Bob")
    assert changes == []
    assert selector.current_user() == "Bob"


def test_user_selector_strips_surrounding_whitespace(qapp):
    selector = UserSelector()
    selector.set_users([])
    changes = []
    selector.user_changed.connect(changes.append)

    selector._combo.setCurrentText("  Jan  ")
    selector._combo.lineEdit().editingFinished.emit()
    assert changes == ["Jan"]


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


def test_profile_selector_sets_capability_notes_as_tooltip(qapp):
    """U7 paper cut: "capability_notes is stored but shown nowhere"."""
    selector = ProfileSelector()
    selector.set_profiles(
        ["argo-claude", "local-ollama"],
        capability_notes={"local-ollama": "small local model — prefer lean MCP groups"},
    )
    assert selector._combo.itemData(0, Qt.ItemDataRole.ToolTipRole) is None
    assert (
        selector._combo.itemData(1, Qt.ItemDataRole.ToolTipRole)
        == "small local model — prefer lean MCP groups"
    )


def test_profile_selector_with_no_capability_notes_sets_no_tooltips(qapp):
    selector = ProfileSelector()
    selector.set_profiles(["argo-claude"])
    assert selector._combo.itemData(0, Qt.ItemDataRole.ToolTipRole) is None


def test_folder_display_shows_none_by_default(qapp):
    display = FolderDisplay()
    assert "(none)" in display._source_label.text()
    assert "(none)" in display._target_label.text()


def test_folder_display_set_folders_updates_labels(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=["/data/a", "/data/b"], target_folder="/out")
    assert "/out" in display._target_label.text()
    assert display.source_folders == ["/data/a", "/data/b"]
    assert display.target_folder == "/out"
    assert display._source_label.isHidden()  # "(none)" placeholder hidden once non-empty

    row_paths = [
        display._source_rows_layout.itemAt(i).widget().path
        for i in range(display._source_rows_layout.count())
    ]
    assert row_paths == ["/data/a", "/data/b"]


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
    monkeypatch.setattr(
        "aida.ui.qt.selectors.QFileDialog.getExistingDirectory", lambda *a, **kw: ""
    )
    changed = []
    display.source_folders_changed.connect(changed.append)

    display._on_add_source_folder()

    assert display.source_folders == []
    assert changed == []


def test_folder_display_add_source_folder_is_idempotent(qapp, monkeypatch, tmp_path):
    display = FolderDisplay()
    monkeypatch.setattr(
        "aida.ui.qt.selectors.QFileDialog.getExistingDirectory", lambda *a, **kw: str(tmp_path)
    )
    changed = []
    display.source_folders_changed.connect(changed.append)

    display._on_add_source_folder()
    display._on_add_source_folder()  # picking the same folder again is a no-op

    assert display.source_folders == [str(tmp_path)]
    assert changed == [[str(tmp_path)]]


# --- Phase 6 bugfix: removing a source folder (previously only possible by
# hand-editing workspaces.yaml) ------------------------------------------


def test_folder_display_remove_button_removes_the_folder(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=["/data/a", "/data/b"], target_folder=None)
    changed = []
    display.source_folders_changed.connect(changed.append)

    row = display._source_rows_layout.itemAt(0).widget()
    assert row.path == "/data/a"
    row.remove_requested.emit("/data/a")

    assert display.source_folders == ["/data/b"]
    assert changed == [["/data/b"]]


def test_folder_display_remove_last_folder_shows_none_placeholder_again(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=["/data/a"], target_folder=None)

    display._on_remove_source_folder("/data/a")

    assert display.source_folders == []
    assert display._source_rows_layout.count() == 0
    assert not display._source_label.isHidden()


def test_folder_display_remove_unknown_folder_is_a_noop(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=["/data/a"], target_folder=None)
    changed = []
    display.source_folders_changed.connect(changed.append)

    display._on_remove_source_folder("/does/not/exist")

    assert display.source_folders == ["/data/a"]
    assert changed == []


def test_folder_display_save_to_workspace_emits_signal(qapp):
    display = FolderDisplay()
    requested = []
    display.save_to_workspace_requested.connect(lambda: requested.append(True))
    display._save_button.click()
    assert requested == [True]


# --- Phase 6: sidecar folder name field ---------------------------------


def test_folder_display_sidecar_defaults_to_figures(qapp):
    display = FolderDisplay()
    assert display.sidecar_folder_name == "figures"
    assert display._sidecar_edit.text() == "figures"


def test_folder_display_set_folders_updates_sidecar_name(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=[], target_folder=None, sidecar_folder_name="images")
    assert display.sidecar_folder_name == "images"
    assert display._sidecar_edit.text() == "images"


def test_folder_display_set_folders_without_sidecar_arg_leaves_it_unchanged(qapp):
    display = FolderDisplay()
    display.set_folders(source_folders=[], target_folder=None, sidecar_folder_name="images")
    display.set_folders(
        source_folders=["/a"], target_folder="/out"
    )  # no sidecar_folder_name this time
    assert display.sidecar_folder_name == "images"


def test_folder_display_editing_sidecar_name_emits_signal(qapp):
    display = FolderDisplay()
    changed = []
    display.sidecar_folder_name_changed.connect(changed.append)

    display._sidecar_edit.setText("plots")
    display._sidecar_edit.editingFinished.emit()

    assert display.sidecar_folder_name == "plots"
    assert changed == ["plots"]


def test_folder_display_editing_sidecar_name_to_blank_reverts(qapp):
    display = FolderDisplay()
    changed = []
    display.sidecar_folder_name_changed.connect(changed.append)

    display._sidecar_edit.setText("   ")
    display._sidecar_edit.editingFinished.emit()

    assert display.sidecar_folder_name == "figures"  # reverted, not blanked
    assert display._sidecar_edit.text() == "figures"
    assert changed == []


def test_folder_display_editing_sidecar_name_no_change_does_not_emit(qapp):
    display = FolderDisplay()
    changed = []
    display.sidecar_folder_name_changed.connect(changed.append)

    display._sidecar_edit.setText("figures")  # same as the default — no real change
    display._sidecar_edit.editingFinished.emit()

    assert changed == []


# --- Phase 9 follow-up: command allowlist + Python interpreter editable
# from the GUI (previously CLI-only via `aida workspace edit`) -------------


def test_folder_display_commands_default_empty(qapp):
    display = FolderDisplay()
    assert display.command_allowlist == []
    assert display.python_interpreter is None
    assert "(none)" in display._command_label.text()


def test_folder_display_set_commands_updates_rows_and_interpreter(qapp):
    display = FolderDisplay()
    display.set_commands(patterns=["git status", "git log *"], interpreter="/opt/env/bin/python")

    assert display.command_allowlist == ["git status", "git log *"]
    assert display.python_interpreter == "/opt/env/bin/python"
    assert display._interpreter_edit.text() == "/opt/env/bin/python"
    assert display._command_label.isHidden()

    row_patterns = [
        display._command_rows_layout.itemAt(i).widget().path
        for i in range(display._command_rows_layout.count())
    ]
    assert row_patterns == ["git status", "git log *"]


def test_folder_display_add_command_emits_signal(qapp):
    display = FolderDisplay()
    changed = []
    display.command_allowlist_changed.connect(changed.append)

    display._command_edit.setText("git status")
    display._on_add_command()

    assert display.command_allowlist == ["git status"]
    assert changed == [["git status"]]
    assert display._command_edit.text() == ""  # cleared after adding


def test_folder_display_add_command_blank_is_a_noop(qapp):
    display = FolderDisplay()
    changed = []
    display.command_allowlist_changed.connect(changed.append)

    display._command_edit.setText("   ")
    display._on_add_command()

    assert display.command_allowlist == []
    assert changed == []


def test_folder_display_add_command_duplicate_is_a_noop(qapp):
    display = FolderDisplay()
    display.set_commands(patterns=["git status"], interpreter=None)
    changed = []
    display.command_allowlist_changed.connect(changed.append)

    display._command_edit.setText("git status")
    display._on_add_command()

    assert display.command_allowlist == ["git status"]
    assert changed == []


def test_folder_display_remove_command_button_removes_it(qapp):
    display = FolderDisplay()
    display.set_commands(patterns=["git status", "git log *"], interpreter=None)
    changed = []
    display.command_allowlist_changed.connect(changed.append)

    row = display._command_rows_layout.itemAt(0).widget()
    assert row.path == "git status"
    row.remove_requested.emit("git status")

    assert display.command_allowlist == ["git log *"]
    assert changed == [["git log *"]]


def test_folder_display_editing_interpreter_emits_signal(qapp):
    display = FolderDisplay()
    changed = []
    display.python_interpreter_changed.connect(changed.append)

    display._interpreter_edit.setText("/usr/bin/python3")
    display._on_interpreter_edited()

    assert display.python_interpreter == "/usr/bin/python3"
    assert changed == ["/usr/bin/python3"]


def test_folder_display_clearing_interpreter_is_allowed(qapp):
    display = FolderDisplay()
    display.set_commands(patterns=[], interpreter="/usr/bin/python3")
    changed = []
    display.python_interpreter_changed.connect(changed.append)

    display._interpreter_edit.setText("")
    display._on_interpreter_edited()

    assert display.python_interpreter is None  # blank means "use the default"
    assert changed == [""]


def test_folder_display_browse_interpreter_via_dialog(qapp, monkeypatch):
    display = FolderDisplay()
    monkeypatch.setattr(
        "aida.ui.qt.selectors.QFileDialog.getOpenFileName",
        lambda *a, **kw: ("/opt/env/bin/python", ""),
    )
    changed = []
    display.python_interpreter_changed.connect(changed.append)

    display._on_browse_interpreter()

    assert display.python_interpreter == "/opt/env/bin/python"
    assert changed == ["/opt/env/bin/python"]


def test_folder_display_browse_interpreter_dialog_cancelled_does_nothing(qapp, monkeypatch):
    display = FolderDisplay()
    monkeypatch.setattr(
        "aida.ui.qt.selectors.QFileDialog.getOpenFileName", lambda *a, **kw: ("", "")
    )
    changed = []
    display.python_interpreter_changed.connect(changed.append)

    display._on_browse_interpreter()

    assert display.python_interpreter is None
    assert changed == []


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


def test_mcp_quick_panel_checkboxes_are_now_live_controls(qapp):
    """Follow-up to the "ticking a checkbox silently did nothing" bug
    report: v1's fix disabled the checkboxes outright (read-only status,
    "MCP Servers…" as the only real entry point). The actual ask was for
    the checkboxes to work, not just stop lying — they're enabled again."""
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="analysis")
    assert all(box.isEnabled() for box in panel._checkboxes.values())


def test_mcp_quick_panel_checking_a_box_emits_start_requested(qapp):
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="analysis")
    started = []
    panel.server_start_requested.connect(started.append)
    panel._checkboxes["bait"].setChecked(True)
    assert started == ["bait"]


def test_mcp_quick_panel_unchecking_a_box_emits_stop_requested(qapp):
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="analysis")
    stopped = []
    panel.server_stop_requested.connect(stopped.append)
    panel._checkboxes["pyirena"].setChecked(False)
    assert stopped == ["pyirena"]


def test_mcp_quick_panel_set_servers_does_not_emit_requests(qapp):
    """set_servers is a refresh, not a user action — programmatically
    setting the initial checked state must not fire start/stop requests
    right back at whatever just supplied that state (that would be an
    infinite refresh loop through aida.ui.qt.main_window)."""
    panel = McpQuickPanel()
    started = []
    stopped = []
    panel.server_start_requested.connect(started.append)
    panel.server_stop_requested.connect(stopped.append)
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena"], group_name="analysis")
    assert started == []
    assert stopped == []


def test_mcp_quick_panel_enabled_now_means_actually_running(qapp):
    """The checked state is McpQuickPanel's whole point now — it must
    reflect exactly the `enabled` list passed in, regardless of any prior
    state, since aida.ui.qt.main_window feeds it real running-server
    state (McpManager.running_server_names), not the workspace's resolved
    mcp_group."""
    panel = McpQuickPanel()
    panel.set_servers(["pyirena", "bait"], enabled=["pyirena", "bait"], group_name="analysis")
    assert set(panel.enabled_servers()) == {"pyirena", "bait"}
    panel.set_servers(["pyirena", "bait"], enabled=[], group_name="analysis")
    assert panel.enabled_servers() == []


def test_mcp_quick_panel_manage_button_emits_manage_requested(qapp):
    panel = McpQuickPanel()
    requests = []
    panel.manage_requested.connect(lambda: requests.append(True))
    panel._manage_button.click()
    assert requests == [True]


def test_user_selector_is_wide_enough_to_show_a_name(qapp):
    """Bug report: "It fits 2 characters." An editable combo sizes to its
    widest *item*, and on a fresh install the widest item is the empty
    "no user" entry — so the box collapsed to nothing and a name could
    neither be read nor typed."""
    selector = UserSelector()
    selector.set_users([])
    assert selector._combo.minimumContentsLength() >= 12
    assert selector._combo.sizeHint().width() > 80
