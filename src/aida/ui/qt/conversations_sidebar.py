"""``ConversationsSidebar`` (PLAN.md Phase 5): "Conversations sidebar: list
(title, date, workspace), open/resume, delete with confirmation, cleanup
dialog (older-than picker)".

Deliberately dumb about persistence: it's fed plain
``aida.persistence.store.ConversationSummary`` objects
(``set_conversations``) and only emits *requests* (``resume_requested``,
``delete_requested``) — ``aida.ui.qt.main_window`` is the one place that
actually calls into ``aida.persistence``/``aida.cli.conversations``.
"""

from __future__ import annotations

from collections.abc import Iterable

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    Signal,
)


def _row_label(summary: ConversationSummary) -> str:
    title = summary.title or "(untitled)"
    workspace = summary.workspace_name or "-"
    return f"{summary.updated_at}  [{workspace}]  {title}"


class CleanupDialog(QDialog):
    """"Older than N days" picker — a static-method convenience
    (``get_cutoff_days``) mirrors Qt's own ``QInputDialog.getInt`` pattern:
    construct, ask, tear down, all in one call for the common case, while
    the class itself stays directly testable (no ``exec()`` needed) for
    anything more specific."""

    def __init__(self, parent: QWidget | None = None, *, default_days: int = 30) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clean Up Old Conversations")
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._days_spin = QSpinBox(self)
        self._days_spin.setRange(1, 3650)
        self._days_spin.setValue(default_days)
        form.addRow("Delete conversations older than (days):", self._days_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def days(self) -> int:
        return self._days_spin.value()

    @staticmethod
    def get_cutoff_days(parent: QWidget | None = None, *, default_days: int = 30) -> int | None:
        dialog = CleanupDialog(parent, default_days=default_days)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.days()
        return None


class ConversationsSidebar(QWidget):
    resume_requested = Signal(str)  # conversation_id
    delete_requested = Signal(str)  # conversation_id, already confirmed
    cleanup_requested = Signal(int)  # cutoff in days, already confirmed
    # Bug report: "Can we have the chat list in the history column have
    # some kind of names? ... these date/times are not very convenient to
    # use." set_title already exists on ConversationStore (used once, by
    # auto-titling) — this is the missing "rename it again" entry point.
    rename_requested = Signal(str, str)  # conversation_id, new_title

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ids_by_row: list[str] = []
        self._titles_by_row: list[str] = []

        layout = QVBoxLayout(self)
        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        self._resume_button = QPushButton("Resume", self)
        self._resume_button.clicked.connect(self._on_resume_clicked)
        buttons.addWidget(self._resume_button)

        self._delete_button = QPushButton("Delete…", self)
        self._delete_button.clicked.connect(self._on_delete_clicked)
        buttons.addWidget(self._delete_button)

        self._rename_button = QPushButton("Rename…", self)
        self._rename_button.clicked.connect(self._on_rename_clicked)
        buttons.addWidget(self._rename_button)

        self._cleanup_button = QPushButton("Clean Up…", self)
        self._cleanup_button.clicked.connect(self._on_cleanup_clicked)
        buttons.addWidget(self._cleanup_button)
        layout.addLayout(buttons)

    def set_conversations(self, summaries: Iterable[ConversationSummary]) -> None:
        self._list.clear()
        self._ids_by_row = []
        self._titles_by_row = []
        for summary in summaries:
            item = QListWidgetItem(_row_label(summary))
            self._list.addItem(item)
            self._ids_by_row.append(summary.id)
            self._titles_by_row.append(summary.title or "")

    @property
    def count(self) -> int:
        return self._list.count()

    def selected_conversation_id(self) -> str | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._ids_by_row):
            return None
        return self._ids_by_row[row]

    def select_row(self, index: int) -> None:
        self._list.setCurrentRow(index)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        row = self._list.row(item)
        if 0 <= row < len(self._ids_by_row):
            self.resume_requested.emit(self._ids_by_row[row])

    def _on_resume_clicked(self) -> None:
        conv_id = self.selected_conversation_id()
        if conv_id:
            self.resume_requested.emit(conv_id)

    def _on_delete_clicked(self) -> None:
        conv_id = self.selected_conversation_id()
        if not conv_id:
            return
        answer = QMessageBox.question(
            self,
            "Delete Conversation",
            "Delete this conversation? This removes its record, artifacts, and history permanently.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(conv_id)

    def _on_rename_clicked(self) -> None:
        row = self._list.currentRow()
        conv_id = self.selected_conversation_id()
        if not conv_id:
            return
        current_title = self._titles_by_row[row] if 0 <= row < len(self._titles_by_row) else ""
        new_title, ok = QInputDialog.getText(self, "Rename Conversation", "Title:", text=current_title)
        new_title = new_title.strip()
        if ok and new_title:
            self.rename_requested.emit(conv_id, new_title)

    def _on_cleanup_clicked(self) -> None:
        days = CleanupDialog.get_cutoff_days(self)
        if days is not None:
            self.cleanup_requested.emit(days)


__all__ = ["CleanupDialog", "ConversationsSidebar"]
