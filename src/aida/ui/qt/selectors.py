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
    QVBoxLayout,
    QWidget,
    Signal,
)

NO_WORKSPACE_LABEL = "(no workspace)"


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

    def set_profiles(self, names: list[str], *, current: str | None = None) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItems(names)
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
    as the folder pickers above it."""

    source_folders_changed = Signal(list)
    target_folder_changed = Signal(str)
    sidecar_folder_name_changed = Signal(str)
    save_to_workspace_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Folders", parent)
        self._source_folders: list[str] = []
        self._target_folder: str | None = None
        self._sidecar_folder_name: str = "figures"

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

    @property
    def source_folders(self) -> list[str]:
        return list(self._source_folders)

    @property
    def target_folder(self) -> str | None:
        return self._target_folder

    @property
    def sidecar_folder_name(self) -> str:
        return self._sidecar_folder_name

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


class McpQuickPanel(QGroupBox):
    """Shows the current workspace's enabled MCP group and a checkbox per
    server currently known to ``mcp.json`` — full server management
    (add/edit/remove servers, live enable/disable) is Phase 7; this is the
    "quick panel" v1: which servers *would* be enabled next time a session
    starts, reflected as ``enabled_servers_changed``."""

    enabled_servers_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("MCP Servers", parent)
        self._layout = QVBoxLayout(self)
        self._group_label = QLabel("Group: (none)", self)
        self._layout.addWidget(self._group_label)
        self._checkboxes: dict[str, QCheckBox] = {}

    def set_servers(self, server_names: list[str], *, enabled: list[str], group_name: str | None) -> None:
        self._group_label.setText(f"Group: {group_name or '(none)'}")
        for checkbox in self._checkboxes.values():
            checkbox.deleteLater()
        self._checkboxes.clear()

        enabled_set = set(enabled)
        for name in server_names:
            checkbox = QCheckBox(name, self)
            checkbox.setChecked(name in enabled_set)
            checkbox.stateChanged.connect(self._on_toggle)
            self._layout.addWidget(checkbox)
            self._checkboxes[name] = checkbox

    def enabled_servers(self) -> list[str]:
        return [name for name, box in self._checkboxes.items() if box.isChecked()]

    def _on_toggle(self, _state: int) -> None:
        self.enabled_servers_changed.emit(self.enabled_servers())


__all__ = ["FolderDisplay", "McpQuickPanel", "ProfileSelector", "WorkspaceSelector"]
