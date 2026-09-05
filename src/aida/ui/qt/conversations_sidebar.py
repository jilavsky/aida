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
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)

ALL_USERS_LABEL = "All users"

#: The conversations that carry no user label at all — everything created
#: before the feature existed, and anything a user cleared. Reachable on
#: its own rather than only via "All users", because otherwise the only way
#: to see unlabelled work is to see *everyone's*.
NO_USER_LABEL = "(no user)"


def _matches(summary: ConversationSummary, query: str) -> bool:
    """Match anything the conversation row visibly identifies.

    The workspace is shown beside the title, so a search that ignored it
    looked broken rather than deliberately narrow.  User labels are useful
    search terms for the same reason.
    """
    haystacks = (summary.title, summary.workspace_name, summary.user)
    return any(query in (value or "").lower() for value in haystacks)


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
    # Bug report: "Enable multiple file selection ... useful for deleting
    # multiple chats." A separate signal from delete_requested (rather than
    # a list there too) keeps every existing single-delete connection/test
    # unchanged — MainWindow just adds one more connection, mirroring
    # _on_cleanup_requested's own "loop then refresh once" shape.
    delete_many_requested = Signal(list)  # list[str] of conversation_ids, already confirmed
    #: (conversation_ids, user) — move chats to a label, "" to unlabel.
    #: The repair for the mistake a free-text label makes easy: having the
    #: wrong name selected when a conversation was started. Nothing else
    #: could fix it — rename_user moves *everything* a name owns.
    move_to_user_requested = Signal(list, str)

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
        #: The active name at the last refresh, so the filter follows a
        #: real switch without stamping on a choice made here.
        self._last_active_user: str | None = None
        self._known_users: list[str] = []

        layout = QVBoxLayout(self)

        # User labels organize shared work; they are not access control.
        # Keep "All users" one click away so selecting a label can never
        # make another person's conversations look lost.
        self._user_filter = QComboBox(self)
        self._user_filter.addItem(ALL_USERS_LABEL)
        self._user_filter.currentTextChanged.connect(
            lambda _text: self._apply_filter(self._search_edit.text())
        )
        layout.addWidget(self._user_filter)

        # U5 bug report follow-up: "the list grows fast in real use" — a
        # substring filter over the title, applied live as the user types.
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Search conversations…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search_edit)

        self._list = QListWidget(self)
        # Bug report: "Enable multiple file selection (usual shift click to
        # select range and ctrl/cmd click to select specific ones) useful
        # for deleting multiple chats." ExtendedSelection is exactly that
        # standard shift-range / ctrl-toggle behavior, built into Qt.
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        # Bug report: "Add meaningful ... button functions to the right
        # click (rename, resume, delete)."
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu_requested)
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

    def set_conversations(
        self, summaries: Iterable[ConversationSummary], *, active_user: str | None = None
    ) -> None:
        """Refresh the list.

        ``active_user`` makes the filter *follow* the toolbar: switching to
        a name in the toolbar and then not having the list change was the
        first thing everybody tried. It only re-selects when the active
        name actually changed since the last refresh, so an explicit
        "All users" (or another name) picked here survives an ordinary
        refresh and is only overridden by a real switch.
        """
        # Bug report: "Let's not add in this list ... conversations which
        # have no messages in them. Currently there are conversations which
        # are empty (were created on start or workspace change and never
        # used)." A ChatSession's recorder creates its conversation row up
        # front, before any message exists (see MainWindow's
        # _delete_conversation_if_empty for the matching auto-delete side
        # of this) — filtering here also retroactively hides any already-
        # accumulated empty rows from *before* that existed, with no
        # migration needed.
        summaries = list(summaries)
        self._all_summaries = [s for s in summaries if s.message_count > 0]
        selected = self._user_filter.currentText()
        names = sorted({summary.user for summary in summaries if summary.user})
        if active_user and active_user not in names:
            # A freshly created name has no conversations yet, but the
            # filter still has to be able to show it — as an empty list,
            # which is the honest answer.
            names = sorted([*names, active_user])
        has_unlabelled = any(summary.user is None for summary in self._all_summaries)

        if active_user is not None and active_user != self._last_active_user:
            selected = active_user or ALL_USERS_LABEL
            self._last_active_user = active_user

        self._user_filter.blockSignals(True)
        self._user_filter.clear()
        self._user_filter.addItem(ALL_USERS_LABEL)
        self._user_filter.addItems(names)
        if has_unlabelled:
            self._user_filter.addItem(NO_USER_LABEL)
        index = self._user_filter.findText(selected)
        self._user_filter.setCurrentIndex(index if index >= 0 else 0)
        self._user_filter.setVisible(bool(names))
        self._user_filter.blockSignals(False)
        self._apply_filter(self._search_edit.text())

    def _apply_filter(self, query: str) -> None:
        query = query.strip().lower()
        visible = (
            self._all_summaries
            if not query
            else [s for s in self._all_summaries if _matches(s, query)]
        )
        selected = self._user_filter.currentText()
        if selected == NO_USER_LABEL:
            visible = [summary for summary in visible if summary.user is None]
        elif selected != ALL_USERS_LABEL:
            # Only this user's conversations — not theirs plus every
            # unlabelled one. Including unlabelled rows here was meant to
            # protect a pre-existing history from vanishing, but since all
            # of that history is unlabelled it made picking a name look
            # like it did nothing at all. "All users" is the safety net,
            # it is one click away, and "(no user)" reaches the unlabelled
            # ones on their own.
            visible = [summary for summary in visible if summary.user == selected]
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

    def selected_conversation_ids(self) -> list[str]:
        """Every currently-selected row's conversation id, in list order
        (not selection/click order) — the multi-select counterpart of
        ``selected_conversation_id`` above, used by bulk Delete."""
        rows = sorted({index.row() for index in self._list.selectedIndexes()})
        return [self._ids_by_row[row] for row in rows if 0 <= row < len(self._ids_by_row)]

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
        """Deletes whatever is currently selected — one conversation
        (``delete_requested``, unchanged from before multi-select existed)
        or several at once (``delete_many_requested``). Shared by the
        Delete… button and the right-click menu's Delete action."""
        conv_ids = self.selected_conversation_ids()
        if not conv_ids:
            return
        if len(conv_ids) == 1:
            title = "Delete Conversation"
            message = "Delete this conversation? This removes its record, artifacts, and history permanently."
        else:
            title = "Delete Conversations"
            message = (
                f"Delete these {len(conv_ids)} conversations? "
                "This removes their records, artifacts, and history permanently."
            )
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if len(conv_ids) == 1:
            self.delete_requested.emit(conv_ids[0])
        else:
            self.delete_many_requested.emit(conv_ids)

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

    def _on_context_menu_requested(self, pos) -> None:
        """Right-click menu — bug report: "Add meaningful (one conversation
        action) button functions to the right click (rename, resume,
        delete)." Right-clicking a row that's already part of the current
        multi-selection acts on that whole selection (standard file-
        manager behavior); right-clicking anywhere else selects just that
        row first, same as a plain left click would."""
        item = self._list.itemAt(pos)
        if item is None:
            return
        if item not in self._list.selectedItems():
            self._list.clearSelection()
            item.setSelected(True)
            self._list.setCurrentItem(item)

        self._popup_context_menu(self._build_context_menu(), self._list.viewport().mapToGlobal(pos))

    def _popup_context_menu(self, menu: QMenu, global_pos) -> None:
        """Split out from ``_on_context_menu_requested`` purely so tests
        can monkeypatch this one call: ``QMenu.exec()`` itself is a
        compiled Qt slot, not overridable via a plain Python monkeypatch,
        and would otherwise pop up a real modal menu that blocks waiting
        for mouse input no automated test can provide."""
        menu.exec(global_pos)

    def set_known_users(self, names: list[str]) -> None:
        """The names offered by the context menu's "Move to User" submenu.
        Supplied by the window rather than read here, so the menu offers
        exactly what the toolbar offers — including a name declared but not
        yet used by any conversation."""
        self._known_users = list(names)

    def _on_move_to_user(self, user: str) -> None:
        ids = self.selected_conversation_ids()
        if ids:
            self.move_to_user_requested.emit(ids, user)

    def _on_move_to_new_user(self) -> None:
        ids = self.selected_conversation_ids()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "Move to User", "Name:")
        if ok and name.strip():
            self.move_to_user_requested.emit(ids, name.strip())

    def _add_move_to_user_menu(self, menu: QMenu) -> QMenu:
        submenu = menu.addMenu("Move to User")
        for name in self._known_users:
            submenu.addAction(name, lambda checked=False, n=name: self._on_move_to_user(n))
        if self._known_users:
            submenu.addSeparator()
        submenu.addAction(NO_USER_LABEL, lambda checked=False: self._on_move_to_user(""))
        submenu.addAction("New user…", lambda checked=False: self._on_move_to_new_user())
        return submenu

    def _build_context_menu(self) -> QMenu:
        """Split out from ``_on_context_menu_requested`` so tests can
        inspect the built menu's actions without popping up a real, modal
        native menu (``exec()`` blocks for real mouse/keyboard input).
        Resume/Rename only make sense for exactly one conversation; a
        multi-selection gets Delete only."""
        menu = QMenu(self)
        if len(self.selected_conversation_ids()) == 1:
            menu.addAction("Resume", self._on_resume_clicked)
            menu.addAction("Rename…", self._on_rename_clicked)
            menu.addSeparator()
        # Offered for a multi-selection too: putting a run of chats under
        # the right name is exactly when several are wrong at once.
        self._add_move_to_user_menu(menu)
        menu.addSeparator()
        menu.addAction("Delete…", self._on_delete_clicked)
        return menu


__all__ = ["CleanupDialog", "ConversationsSidebar"]
