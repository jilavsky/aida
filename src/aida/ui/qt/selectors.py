"""Toolbar switchers (PLAN.md Phase 5): workspace selector, profile
selector, source/target folder display, and the MCP quick panel.

Each widget here only knows about plain data (workspace/profile/server
names) and emits a signal when the user changes something — it never talks
to ``ChatBridge`` or ``aida.workspace``/``aida.config`` directly. That
wiring (switching mid-session starting a new conversation after
confirmation, actually persisting a "save to workspace" edit, etc.) belongs
to ``aida.ui.qt.main_window``, which is the one place that's allowed to
know about both the GUI and the session/config layers at once.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)

NO_WORKSPACE_LABEL = "(no workspace)"


class UserSelector(QWidget):
    """Who (or what) new conversations are labelled with.

    Editable on purpose: names are not registered anywhere — one exists
    because a conversation used it — so typing is how the first
    conversation for a new person or project is created.
    """

    user_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("User:", self))
        self._combo = QComboBox(self)
        self._combo.setEditable(True)
        self._combo.currentTextChanged.connect(self.user_changed.emit)
        layout.addWidget(self._combo)

    def set_users(self, names: list[str], *, current: str | None = None) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem("")
        self._combo.addItems(names)
        self._combo.setCurrentText(current or "")
        self._combo.blockSignals(False)

    def current_user(self) -> str:
        return self._combo.currentText()


class WorkspaceSelector(QWidget):
    """A dropdown of configured workspace names, plus an explicit
    "(no workspace)" option — switching triggers ``workspace_changed`` with
    ``""`` for that option, a real name otherwise. Doesn't decide what
    switching *does* (that's a new conversation, after confirmation, per
    PLAN.md) — just reports the choice."""

    workspace_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Workspace:", self))
        self._combo = QComboBox(self)
        self._combo.currentTextChanged.connect(self._on_changed)
        layout.addWidget(self._combo)

    def set_workspaces(self, names: list[str], *, current: str | None = None) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem(NO_WORKSPACE_LABEL)
        self._combo.addItems(names)
        if current:
            index = self._combo.findText(current)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)

    def current_workspace(self) -> str:
        text = self._combo.currentText()
        return "" if text == NO_WORKSPACE_LABEL else text

    def _on_changed(self, text: str) -> None:
        self.workspace_changed.emit("" if text == NO_WORKSPACE_LABEL else text)


class ProfileSelector(QWidget):
    """A dropdown of configured provider profile names. Switching mid
    conversation is allowed (Phase 2 semantics — history carries over)."""

    profile_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Profile:", self))
        self._combo = QComboBox(self)
        self._combo.currentTextChanged.connect(self._on_changed)
        layout.addWidget(self._combo)

    def set_profiles(
        self, names: list[str], *, current: str | None = None, capability_notes: dict[str, str] | None = None
    ) -> None:
        """``capability_notes`` (U7 paper cut: "capability_notes is stored
        but shown nowhere") sets each entry's tooltip to its profile's
        ``ProviderProfile.capability_notes``, when non-empty — e.g. "small
        local model — prefer lean MCP groups"."""
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItems(names)
        notes = capability_notes or {}
        for index, name in enumerate(names):
            note = notes.get(name)
            if note:
                self._combo.setItemData(index, note, Qt.ItemDataRole.ToolTipRole)
        if current:
            index = self._combo.findText(current)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)

    def current_profile(self) -> str:
        return self._combo.currentText()

    def set_current(self, name: str) -> None:
        index = self._combo.findText(name)
        if index >= 0:
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(index)
            self._combo.blockSignals(False)

    def _on_changed(self, text: str) -> None:
        if text:
            self.profile_changed.emit(text)


class _RemovableFolderRow(QWidget):
    """One "path × Remove" row in the source-folders list — the bug report
    this addresses: "Do not seem to be able to remove source_folder [any
    way] other than manually from yaml file"."""

    remove_requested = Signal(str)

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Same fix as aida.ui.qt.tool_call_widget.ToolCallRow's summary
        # label: without word wrap, a long real-world source-folder path
        # (this lab's own paths routinely run 80-100+ characters) has a
        # minimum size hint equal to its full unwrapped width, which this
        # QHBoxLayout propagates straight up to the main window.
        path_label = QLabel(path, self)
        path_label.setWordWrap(True)
        layout.addWidget(path_label, stretch=1)
        remove_button = QPushButton("Remove", self)
        remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))
        layout.addWidget(remove_button)


class FolderDisplay(QGroupBox):
    """Shows the active workspace's source/target folders for *this*
    session — each source folder gets its own row with a Remove button (no
    other way existed to drop one short of hand-editing ``workspaces.yaml``);
    the target folder gets a Change button (native folder picker). Writes
    back only to the in-memory session state until "Save to Workspace" is
    clicked, which persists it (``save_to_workspace_requested``).

    Phase 6 adds the sidecar folder name (where generated report images get
    copied, relative to the target folder — see
    ``aida.documents.writers.md_obsidian``) as a plain editable text field,
    following the same "visible + editable, saved only on request" pattern
    as the folder pickers above it.

    Phase 9 follow-up: the command allowlist and Python interpreter
    (``WorkspaceConfig.command_allowlist``/``.python_interpreter``) were
    CLI-only (``aida workspace edit --command-allowlist ...``) — user
    feedback was that they wanted to manage what ``run_command`` may run
    without confirmation from the same place they already manage folders,
    rather than dropping to a terminal. Reuses ``_RemovableFolderRow``
    as-is for allow-pattern rows (it only ever knew about a plain string +
    Remove button, never anything folder-specific)."""

    source_folders_changed = Signal(list)
    target_folder_changed = Signal(str)
    sidecar_folder_name_changed = Signal(str)
    command_allowlist_changed = Signal(list)
    python_interpreter_changed = Signal(str)
    save_to_workspace_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        # Renamed from "Folders" — this group box covers everything a
        # session is allowed to touch for the active workspace (source/
        # target folders, the command allowlist, the Python interpreter),
        # not just folders; "Workspace permissions" says that plainly.
        super().__init__("Workspace permissions", parent)
        self._source_folders: list[str] = []
        self._target_folder: str | None = None
        self._sidecar_folder_name: str = "figures"
        self._command_patterns: list[str] = []
        self._python_interpreter: str = ""

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Source folders:", self))
        self._source_label = QLabel("(none)", self)  # shown only when the list is empty
        layout.addWidget(self._source_label)
        self._source_rows_layout = QVBoxLayout()
        layout.addLayout(self._source_rows_layout)
        add_source_button = QPushButton("Add Source Folder…", self)
        add_source_button.clicked.connect(self._on_add_source_folder)
        layout.addWidget(add_source_button)

        target_row = QHBoxLayout()
        self._target_label = QLabel("Target: (none)", self)
        self._target_label.setWordWrap(True)
        target_row.addWidget(self._target_label, stretch=1)
        change_target_button = QPushButton("Change Target Folder…", self)
        change_target_button.clicked.connect(self._on_change_target_folder)
        target_row.addWidget(change_target_button)
        layout.addLayout(target_row)

        sidecar_row = QHBoxLayout()
        sidecar_row.addWidget(QLabel("Sidecar folder:", self))
        self._sidecar_edit = QLineEdit(self._sidecar_folder_name, self)
        self._sidecar_edit.editingFinished.connect(self._on_sidecar_edited)
        sidecar_row.addWidget(self._sidecar_edit)
        layout.addLayout(sidecar_row)

        layout.addWidget(QLabel("Allowed commands (run_command needs no confirmation for these):", self))
        self._command_label = QLabel("(none)", self)  # shown only when the list is empty
        layout.addWidget(self._command_label)
        self._command_rows_layout = QVBoxLayout()
        layout.addLayout(self._command_rows_layout)
        add_command_row = QHBoxLayout()
        self._command_edit = QLineEdit(self)
        self._command_edit.setPlaceholderText("e.g. git status  or  git log *")
        add_command_row.addWidget(self._command_edit, stretch=1)
        add_command_button = QPushButton("Add Command", self)
        add_command_button.clicked.connect(self._on_add_command)
        add_command_row.addWidget(add_command_button)
        layout.addLayout(add_command_row)

        interpreter_row = QHBoxLayout()
        interpreter_row.addWidget(QLabel("Python interpreter:", self))
        self._interpreter_edit = QLineEdit(self)
        self._interpreter_edit.setPlaceholderText("(default: the interpreter AIDA itself runs under)")
        self._interpreter_edit.editingFinished.connect(self._on_interpreter_edited)
        interpreter_row.addWidget(self._interpreter_edit, stretch=1)
        browse_interpreter_button = QPushButton("Browse…", self)
        browse_interpreter_button.clicked.connect(self._on_browse_interpreter)
        interpreter_row.addWidget(browse_interpreter_button)
        layout.addLayout(interpreter_row)

        self._save_button = QPushButton("Save to Workspace", self)
        self._save_button.clicked.connect(self.save_to_workspace_requested.emit)
        layout.addWidget(self._save_button)

    def set_folders(
        self, *, source_folders: list[str], target_folder: str | None, sidecar_folder_name: str | None = None
    ) -> None:
        self._source_folders = list(source_folders)
        self._target_folder = target_folder
        self._target_label.setText("Target: " + (target_folder or "(none)"))
        if sidecar_folder_name is not None:
            self._sidecar_folder_name = sidecar_folder_name
            self._sidecar_edit.setText(sidecar_folder_name)
        self._refresh_source_rows()

    def _refresh_source_rows(self) -> None:
        while self._source_rows_layout.count():
            item = self._source_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._source_label.setVisible(not self._source_folders)
        self._source_label.setText("(none)")
        for folder in self._source_folders:
            row = _RemovableFolderRow(folder, self)
            row.remove_requested.connect(self._on_remove_source_folder)
            self._source_rows_layout.addWidget(row)

    def set_commands(self, *, patterns: list[str], interpreter: str | None) -> None:
        self._command_patterns = list(patterns)
        self._python_interpreter = interpreter or ""
        self._interpreter_edit.setText(self._python_interpreter)
        self._refresh_command_rows()

    def _refresh_command_rows(self) -> None:
        while self._command_rows_layout.count():
            item = self._command_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._command_label.setVisible(not self._command_patterns)
        for pattern in self._command_patterns:
            row = _RemovableFolderRow(pattern, self)
            row.remove_requested.connect(self._on_remove_command)
            self._command_rows_layout.addWidget(row)

    @property
    def source_folders(self) -> list[str]:
        return list(self._source_folders)

    @property
    def target_folder(self) -> str | None:
        return self._target_folder

    @property
    def sidecar_folder_name(self) -> str:
        return self._sidecar_folder_name

    @property
    def command_allowlist(self) -> list[str]:
        return list(self._command_patterns)

    @property
    def python_interpreter(self) -> str | None:
        return self._python_interpreter or None

    def _on_add_source_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Source Folder")
        if not folder:
            return
        if folder in self._source_folders:
            return
        self._source_folders.append(folder)
        self.set_folders(source_folders=self._source_folders, target_folder=self._target_folder)
        self.source_folders_changed.emit(self._source_folders)

    def _on_remove_source_folder(self, folder: str) -> None:
        if folder not in self._source_folders:
            return
        self._source_folders.remove(folder)
        self.set_folders(source_folders=self._source_folders, target_folder=self._target_folder)
        self.source_folders_changed.emit(self._source_folders)

    def _on_change_target_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Change Target Folder")
        if not folder:
            return
        self._target_folder = folder
        self.set_folders(source_folders=self._source_folders, target_folder=folder)
        self.target_folder_changed.emit(folder)

    def _on_sidecar_edited(self) -> None:
        name = self._sidecar_edit.text().strip()
        if not name or name == self._sidecar_folder_name:
            self._sidecar_edit.setText(self._sidecar_folder_name)  # revert an empty/no-op edit
            return
        self._sidecar_folder_name = name
        self.sidecar_folder_name_changed.emit(name)

    def _on_add_command(self) -> None:
        pattern = self._command_edit.text().strip()
        if not pattern or pattern in self._command_patterns:
            return
        self._command_patterns.append(pattern)
        self._command_edit.clear()
        self._refresh_command_rows()
        self.command_allowlist_changed.emit(self._command_patterns)

    def _on_remove_command(self, pattern: str) -> None:
        if pattern not in self._command_patterns:
            return
        self._command_patterns.remove(pattern)
        self._refresh_command_rows()
        self.command_allowlist_changed.emit(self._command_patterns)

    def _on_browse_interpreter(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Python Interpreter", self._interpreter_edit.text())
        if not path:
            return
        self._interpreter_edit.setText(path)
        self._python_interpreter = path
        self.python_interpreter_changed.emit(path)

    def _on_interpreter_edited(self) -> None:
        text = self._interpreter_edit.text().strip()
        if text == self._python_interpreter:
            return
        self._python_interpreter = text
        self.python_interpreter_changed.emit(text)


class McpQuickPanel(QGroupBox):
    """Shows a checkbox per server currently known to ``mcp.json`` and lets
    the user start/stop each one directly — full server management
    (add/edit/remove servers, tool inspection, logs) is still Phase 7's
    ``McpManagementDialog``, reachable via the "MCP Servers…" button below
    the checkboxes.

    v1 shipped these checkboxes as if ticking one would change which
    servers start, but nothing was wired up — a real bug, fixed by
    disabling them (2026-08-22 note above the fix in
    planning/improvement_plan_2026-08.md §1: "a misleading control is worse
    than no control"). This is the follow-up the user actually wanted
    instead of the read-only compromise: ticking/unticking now really
    starts/stops that server (``server_start_requested``/
    ``server_stop_requested`` — ``aida.ui.qt.main_window`` is the one place
    that turns those into real ``ChatBridge.start_mcp_server``/
    ``stop_mcp_server`` calls, same live-control pattern
    ``McpManagementDialog`` already used). Checked state is not
    self-maintained: it always reflects whatever ``set_servers`` was last
    told is actually running, so a failed start (bad command, server
    crashed) or a stop from the full management dialog shows up here
    correctly on the next refresh rather than the checkbox silently lying
    about what's really running.
    """

    enabled_servers_changed = Signal(list)
    server_start_requested = Signal(str)  # server name — user checked the box
    server_stop_requested = Signal(str)  # server name — user unchecked the box
    manage_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("MCP Servers", parent)
        self._layout = QVBoxLayout(self)
        self._group_label = QLabel("Group: (none)", self)
        self._layout.addWidget(self._group_label)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._manage_button = QPushButton("MCP Servers…", self)
        self._manage_button.clicked.connect(self.manage_requested)
        self._layout.addWidget(self._manage_button)

    def set_servers(self, server_names: list[str], *, enabled: list[str], group_name: str | None) -> None:
        """``enabled`` is which servers are *actually running right now*
        (``aida.mcp.manager.McpManager.running_server_names``), not merely
        which ones the workspace's ``mcp_group`` would resolve to — the
        checkbox is a live control now, so its checked state has to mean
        "running", not "would start". Rebuilding the checkboxes here (own
        signals blocked) rather than just calling ``setChecked`` on
        existing ones is deliberately the same "clear and rebuild" pattern
        as before, so a changed server list (workspace switch, a server
        added/removed via the management dialog) is always handled
        correctly too."""
        self._group_label.setText(f"Group: {group_name or '(none)'}")
        for checkbox in self._checkboxes.values():
            checkbox.deleteLater()
        self._checkboxes.clear()

        enabled_set = set(enabled)
        for name in server_names:
            checkbox = QCheckBox(name, self)
            checkbox.blockSignals(True)
            checkbox.setChecked(name in enabled_set)
            checkbox.blockSignals(False)
            checkbox.toggled.connect(lambda checked, name=name: self._on_toggle(name, checked))
            self._layout.insertWidget(self._layout.count() - 1, checkbox)
            self._checkboxes[name] = checkbox

    def enabled_servers(self) -> list[str]:
        return [name for name, box in self._checkboxes.items() if box.isChecked()]

    def _on_toggle(self, name: str, checked: bool) -> None:
        if checked:
            self.server_start_requested.emit(name)
        else:
            self.server_stop_requested.emit(name)
        self.enabled_servers_changed.emit(self.enabled_servers())


__all__ = ["FolderDisplay", "McpQuickPanel", "ProfileSelector", "WorkspaceSelector"]
