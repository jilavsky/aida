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
from datetime import datetime

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    Signal,
)


def _format_timestamp(iso_str: str) -> str:
    """Local short date/time for a sidebar row — bug report: "these
    date/times are not very convenient to use" (row labels used to show the
    raw UTC ISO-8601 string, e.g. "2026-08-22T14:03:22.123456+00:00...").
    ``updated_at`` is always written by ``aida.persistence.recorder._now_iso``
    (``datetime.now(UTC).isoformat()``); shown here converted to the
    viewer's own local timezone as e.g. "Aug 22 09:03". Falls back to the
    raw string on anything unparseable — a hand-edited or foreign DB row
    must not crash the sidebar."""
    try:
        parsed = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return iso_str
    return parsed.astimezone().strftime("%b %d %H:%M")


def _row_label(summary: ConversationSummary) -> str:
    title = summary.title or "(untitled)"
    workspace = summary.workspace_name or "-"
    when = _format_timestamp(summary.updated_at)
    return f"{when}  [{workspace}]  {title}"


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
        # U5: the full, unfiltered set from the last set_conversations() —
        # _apply_filter() re-derives the visible rows from this, so a
        # refresh (resume/delete/rename/cleanup all call set_conversations
        # again) re-applies whatever the user has typed instead of
        # silently clearing it.
        self._all_summaries: list[ConversationSummary] = []

        layout = QVBoxLayout(self)

        # U5 bug report follow-up: "the list grows fast in real use" — a
        # substring filter over the title, applied live as the user types.
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Search conversations…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search_edit)

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
        self._all_summaries = list(summaries)
        self._apply_filter(self._search_edit.text())

    def _apply_filter(self, query: str) -> None:
        query = query.strip().lower()
        visible = (
            self._all_summaries
            if not query
            else [s for s in self._all_summaries if query in (s.title or "").lower()]
        )
        self._list.clear()
        self._ids_by_row = []
        self._titles_by_row = []
        for summary in visible:
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
