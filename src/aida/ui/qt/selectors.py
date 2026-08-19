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


class FolderDisplay(QGroupBox):
    """Shows the active workspace's source/target folders for *this*
    session, each with a Change button (native folder picker) — writes back
    only to the in-memory session state until "Save to Workspace" is
    clicked, which persists it (``save_to_workspace_requested``)."""

    source_folders_changed = Signal(list)
    target_folder_changed = Signal(str)
    save_to_workspace_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Folders", parent)
        self._source_folders: list[str] = []
        self._target_folder: str | None = None

        layout = QVBoxLayout(self)

        source_row = QHBoxLayout()
        self._source_label = QLabel("Source: (none)", self)
        source_row.addWidget(self._source_label)
        add_source_button = QPushButton("Add Source Folder…", self)
        add_source_button.clicked.connect(self._on_add_source_folder)
        source_row.addWidget(add_source_button)
        layout.addLayout(source_row)

        target_row = QHBoxLayout()
        self._target_label = QLabel("Target: (none)", self)
        target_row.addWidget(self._target_label)
        change_target_button = QPushButton("Change Target Folder…", self)
        change_target_button.clicked.connect(self._on_change_target_folder)
        target_row.addWidget(change_target_button)
        layout.addLayout(target_row)

        self._save_button = QPushButton("Save to Workspace", self)
        self._save_button.clicked.connect(self.save_to_workspace_requested.emit)
        layout.addWidget(self._save_button)

    def set_folders(self, *, source_folders: list[str], target_folder: str | None) -> None:
        self._source_folders = list(source_folders)
        self._target_folder = target_folder
        self._source_label.setText("Source: " + (", ".join(source_folders) or "(none)"))
        self._target_label.setText("Target: " + (target_folder or "(none)"))

    @property
    def source_folders(self) -> list[str]:
        return list(self._source_folders)

    @property
    def target_folder(self) -> str | None:
        return self._target_folder

    def _on_add_source_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Source Folder")
        if not folder:
            return
        self._source_folders.append(folder)
        self.set_folders(source_folders=self._source_folders, target_folder=self._target_folder)
        self.source_folders_changed.emit(self._source_folders)

    def _on_change_target_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Change Target Folder")
        if not folder:
            return
        self._target_folder = folder
        self.set_folders(source_folders=self._source_folders, target_folder=folder)
        self.target_folder_changed.emit(folder)


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
