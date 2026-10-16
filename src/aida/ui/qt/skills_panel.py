"""``SkillsPanel``: tick which skills the running chat uses.

User request (2026-10): a paper-review workspace with separate skills for
summarization, review and related-work search. AIDA pastes every active
skill whole into the system message, so leaving all of them on gives the
model several competing playbooks at once. This panel lists every skill in
the skills folder with a checkbox; each toggle is applied to the running
chat straight away (``skills_changed`` → ``ChatBridge.update_skills``), and
"Save to Workspace" keeps the selection for future chats — the same
"applied now, saved on request" split as the folder panel
(``aida.ui.qt.selectors.FolderDisplay``).

Deliberately dumb about persistence and the skills folder, like every other
widget in ``aida.ui.qt``: ``aida.ui.qt.main_window`` hands it plain
``SkillData`` values and does the reading/saving.
"""

from __future__ import annotations

from dataclasses import dataclass

from aida.ui.qt._qt import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    Signal,
)
from aida.ui.qt.selectors import MCP_PANEL_COLUMNS


@dataclass
class SkillData:
    """One skill as the panel shows it."""

    name: str
    tooltip: str = ""


class SkillsPanel(QGroupBox):
    skills_changed = Signal(list)  # list[str], checked skill names — apply to the chat
    reload_requested = Signal()  # re-list the skills folder and re-read the files
    save_to_workspace_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Skills", parent)

        layout = QVBoxLayout(self)
        self._empty_label = QLabel("(no skills in the skills folder)", self)
        layout.addWidget(self._empty_label)
        # Same two-column checkbox grid as the MCP Servers panel
        # (``McpQuickPanel``), to keep the right-hand column short.
        self._grid = QGridLayout()
        layout.addLayout(self._grid)
        self._checkboxes: dict[str, QCheckBox] = {}

        buttons = QHBoxLayout()
        self._reload_button = QPushButton("Reload", self)
        self._reload_button.setToolTip(
            "Re-scan the skills folder and re-read the active skill files into this chat"
        )
        self._reload_button.clicked.connect(self.reload_requested.emit)
        buttons.addWidget(self._reload_button)
        self._save_button = QPushButton("Save to Workspace", self)
        self._save_button.setToolTip("Start future chats in this workspace with these skills")
        self._save_button.clicked.connect(self.save_to_workspace_requested.emit)
        buttons.addWidget(self._save_button)
        layout.addLayout(buttons)

        self._unsaved_label = QLabel("", self)
        self._unsaved_label.setWordWrap(True)
        layout.addWidget(self._unsaved_label)
        self.set_unsaved(False)

    def set_skills(self, available: list[SkillData], active: list[str]) -> None:
        """Show ``available`` with the names in ``active`` ticked. An active
        name with no file behind it isn't listed — there is nothing to load."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent before deleting: `takeAt` only removes the widget
                # from the layout, so until `deleteLater` is serviced it is
                # still a child of this panel and still painted at its old
                # geometry — a Reload with a changed skill list flashes the
                # stale checkboxes over the new ones. Clearing the parent
                # hides it in the same event-loop turn the rebuild happens in.
                widget.setParent(None)
                widget.deleteLater()
        self._checkboxes.clear()

        active_set = set(active)
        # Column-major, as in McpQuickPanel.set_servers: names run down the
        # left column and continue down the right, so the list still reads
        # alphabetically.
        rows = max(1, (len(available) + MCP_PANEL_COLUMNS - 1) // MCP_PANEL_COLUMNS)
        for index, skill in enumerate(available):
            checkbox = QCheckBox(skill.name, self)
            checkbox.setChecked(skill.name in active_set)
            if skill.tooltip:
                checkbox.setToolTip(skill.tooltip)
            checkbox.toggled.connect(self._on_toggled)
            self._grid.addWidget(checkbox, index % rows, index // rows)
            self._checkboxes[skill.name] = checkbox
        self._empty_label.setVisible(not available)

    def checked_skills(self) -> list[str]:
        return [name for name, box in self._checkboxes.items() if box.isChecked()]

    def set_save_enabled(self, enabled: bool) -> None:
        self._save_button.setEnabled(enabled)

    def set_unsaved(self, unsaved: bool) -> None:
        """Same wording as the folder panel's — see ``FolderDisplay.set_unsaved``."""
        self._save_button.setText("Save to Workspace *" if unsaved else "Save to Workspace")
        self._unsaved_label.setText(
            "Applied to this chat. Click Save to Workspace to keep it for future chats."
            if unsaved
            else ""
        )
        self._unsaved_label.setVisible(unsaved)

    def _on_toggled(self, _checked: bool) -> None:
        self.skills_changed.emit(self.checked_skills())
