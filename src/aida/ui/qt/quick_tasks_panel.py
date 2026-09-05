"""``QuickTasksPanel`` (B14): a workspace-scoped list of short, reusable
prompt templates for routine tasks. User request: "Some workspaces may have
set of routine tasks which I would like to add to some kind of quick
selection methods. These must be Workspace specific, somehow editable
(add/remove/edit) and easily usable... I hope to have at least 5-10 slots."

Double-click a row to drop its text into the input box
(``task_selected`` — ``aida.ui.qt.main_window`` wires this to
``InputBox.set_text``, not auto-send, so the user can review/fill in
details like a sample name or scan number before sending). Right-click for
Add/Edit/Delete, the same testable ``_build_context_menu``/
``_popup_context_menu`` split ``ConversationsSidebar`` (B12) uses — a real
``QMenu.exec()`` is a compiled Qt slot that can't be reliably monkeypatched
directly in a test, and would otherwise pop up a real modal menu blocking
for mouse input no automated test can provide.

Deliberately dumb about persistence, same as every other widget in
``aida.ui.qt``: it only knows plain ``QuickTaskData`` values and emits
``tasks_changed`` after any edit — ``aida.ui.qt.main_window`` is the one
place that reads/writes the active workspace's own ``WorkspaceConfig.
quick_tasks``.
"""

from __future__ import annotations

from dataclasses import dataclass

from aida.ui.qt._qt import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)

#: Soft cap on how many quick tasks this panel's own Add dialog will create
#: per workspace (user request: "at least 5-10 slots... we likely do not
#: need infinite number as that seems ridiculous"). Not enforced by the
#: config loader (``aida.config.settings._coerce_quick_tasks``) — a hand-
#: edited ``workspaces.yaml`` with more than this still loads every one of
#: them; this only disables the panel's "Add…" action once reached.
MAX_QUICK_TASKS = 10


@dataclass
class QuickTaskData:
    """Plain data twin of ``aida.config.settings.QuickTask`` — this module
    doesn't import ``aida.config`` (no widget in ``aida.ui.qt`` talks to
    config/persistence directly), so ``aida.ui.qt.main_window`` converts
    between the two."""

    name: str
    text: str


class QuickTaskEditDialog(QDialog):
    """Add/Edit — same small-modal shape as ``ConversationsSidebar``'s
    ``CleanupDialog``, just two fields instead of one since a routine-task
    template needs both a short label and an often multi-line body."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name: str = "",
        text: str = "",
        title: str = "Add Quick Task",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._name_edit = QLineEdit(name, self)
        form.addRow("Name:", self._name_edit)
        layout.addLayout(form)

        self._text_edit = QPlainTextEdit(text, self)
        self._text_edit.setPlaceholderText("The prompt text to drop into the conversation…")
        layout.addWidget(self._text_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def name(self) -> str:
        return self._name_edit.text().strip()

    def text(self) -> str:
        return self._text_edit.toPlainText().strip()

    @staticmethod
    def get_task(
        parent: QWidget | None = None,
        *,
        name: str = "",
        text: str = "",
        title: str = "Add Quick Task",
    ) -> tuple[str, str] | None:
        """Construct, ask, tear down — mirrors ``CleanupDialog.
        get_cutoff_days``'s convenience shape. Returns ``None`` on Cancel,
        or if either field is left blank (a nameless or empty-bodied quick
        task isn't useful and would show as a blank row in the list)."""
        dialog = QuickTaskEditDialog(parent, name=name, text=text, title=title)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_name, new_text = dialog.name(), dialog.text()
            if new_name and new_text:
                return new_name, new_text
        return None


class QuickTasksPanel(QGroupBox):
    task_selected = Signal(str)  # the task's text, for the input box
    tasks_changed = Signal(list)  # list[QuickTaskData], full replacement — persist this

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Quick Tasks", parent)
        self._tasks: list[QuickTaskData] = []

        layout = QVBoxLayout(self)
        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu_requested)
        layout.addWidget(self._list)

    def set_tasks(self, tasks: list[QuickTaskData]) -> None:
        self._tasks = list(tasks)
        self._list.clear()
        for task in self._tasks:
            self._list.addItem(QListWidgetItem(task.name))

    def tasks(self) -> list[QuickTaskData]:
        return list(self._tasks)

    @property
    def count(self) -> int:
        return len(self._tasks)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        row = self._list.row(item)
        if 0 <= row < len(self._tasks):
            self.task_selected.emit(self._tasks[row].text)

    def _on_context_menu_requested(self, pos) -> None:
        item = self._list.itemAt(pos)
        self._popup_context_menu(
            self._build_context_menu(item), self._list.viewport().mapToGlobal(pos)
        )

    def _popup_context_menu(self, menu: QMenu, global_pos) -> None:
        """Split out purely so tests can monkeypatch this one call instead
        of the compiled ``QMenu.exec()`` slot — see ``ConversationsSidebar.
        _popup_context_menu``'s docstring for why."""
        menu.exec(global_pos)

    def _build_context_menu(self, item: QListWidgetItem | None) -> QMenu:
        """Split out so tests can inspect the built menu without popping up
        a real, modal native menu. Edit/Delete only appear when right-
        clicking an actual row; Add… is always offered (disabled past the
        slot cap)."""
        menu = QMenu(self)
        if item is not None:
            row = self._list.row(item)
            menu.addAction("Edit…", lambda: self._on_edit(row))
            menu.addAction("Delete…", lambda: self._on_delete(row))
            menu.addSeparator()
        add_action = menu.addAction("Add…", self._on_add)
        if len(self._tasks) >= MAX_QUICK_TASKS:
            add_action.setEnabled(False)
            add_action.setToolTip(f"Up to {MAX_QUICK_TASKS} quick tasks per workspace")
        return menu

    def _on_add(self) -> None:
        if len(self._tasks) >= MAX_QUICK_TASKS:
            return
        result = QuickTaskEditDialog.get_task(self, title="Add Quick Task")
        if result is None:
            return
        name, text = result
        self._tasks.append(QuickTaskData(name=name, text=text))
        self.set_tasks(self._tasks)
        self.tasks_changed.emit(self.tasks())

    def _on_edit(self, row: int) -> None:
        if not (0 <= row < len(self._tasks)):
            return
        current = self._tasks[row]
        result = QuickTaskEditDialog.get_task(
            self, name=current.name, text=current.text, title="Edit Quick Task"
        )
        if result is None:
            return
        name, text = result
        self._tasks[row] = QuickTaskData(name=name, text=text)
        self.set_tasks(self._tasks)
        self.tasks_changed.emit(self.tasks())

    def _on_delete(self, row: int) -> None:
        if not (0 <= row < len(self._tasks)):
            return
        task = self._tasks[row]
        answer = QMessageBox.question(
            self,
            "Delete Quick Task",
            f"Delete the quick task {task.name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._tasks[row]
        self.set_tasks(self._tasks)
        self.tasks_changed.emit(self.tasks())


__all__ = ["MAX_QUICK_TASKS", "QuickTaskData", "QuickTaskEditDialog", "QuickTasksPanel"]
