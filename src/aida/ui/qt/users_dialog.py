"""Manage the conversation ``user`` labels — rename, merge, clear.

**Why this dialog has to exist.** The toolbar's user box is free text, and
nothing validates a typed name: "Jan", "jan" and "Jam" are three different
people as far as the database is concerned. Without a repair operation, one
typo permanently splits a person's history and there is no way to put it
back together from inside the app. Renaming onto a name that already exists
is a *merge* on purpose — that is precisely what fixing a typo means.

**"New User" here means "start using this name."** There is no registry to
add a row to — a name exists because a conversation carries it — so what
this button actually does is make the name *active*, and the next
conversation is what brings it into existence. That is a real thing to
offer even so: without it the only way to start using a name is to know
that the toolbar box is editable, which nobody discovers, and a dialog
called "Manage Users" that cannot get you a user is a dead end. The active
name is shown in the list even before it has any conversations, so it does
not look as though nothing happened.

**There is no "delete user".** Clearing a label leaves the conversations in
place and visible to everyone, which is what removing a *label* means.
Deleting somebody's work is a louder, different operation, and it already
lives in the conversations sidebar — duplicating a destructive action in
two places is how the wrong one gets clicked.
"""

from __future__ import annotations

from aida.persistence.store import ConversationStore
from aida.ui.qt._qt import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


class UsersDialog(QDialog):
    """Lists every label in use with its conversation count.

    Takes a ``ConversationStore`` rather than opening its own so a caller
    (and a test) controls the database being edited, matching how the other
    management dialogs are wired.
    """

    def __init__(
        self,
        store: ConversationStore,
        parent: QWidget | None = None,
        *,
        active_user: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Users")
        self._store = store
        self._active_user = active_user
        #: Set when something changed, so the caller knows to refresh the
        #: sidebar and the toolbar box rather than guessing.
        self.changed = False
        #: Set when the user asked to start working under a different name;
        #: the caller switches to it (which starts a new chat, the same as
        #: the toolbar box does). ``None`` means "no switch requested".
        self.new_active_user: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Labels for grouping conversations — a person on a shared machine, or a "
                "project. A name comes into existence when a conversation uses it.\n"
                "This is organization, not security: anyone can pick any name, and it "
                "does not restrict access to anything.",
                self,
            )
        )

        self._list = QListWidget(self)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._list)

        #: Shown instead of an empty box, because an empty list plus two
        #: greyed-out buttons reads as a broken dialog rather than an empty
        #: one.
        self._empty_label = QLabel(
            "No names in use yet. “New User…” starts one — the next conversation you "
            "have will be labelled with it.",
            self,
        )
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        buttons = QHBoxLayout()
        self._new_button = QPushButton("New User…", self)
        self._new_button.setToolTip(
            "Start labelling new conversations with a name. Nothing is registered "
            "anywhere — the name exists from the moment a conversation uses it."
        )
        self._new_button.clicked.connect(self._on_new)
        buttons.addWidget(self._new_button)

        self._rename_button = QPushButton("Rename or Merge…", self)
        self._rename_button.clicked.connect(self._on_rename)
        buttons.addWidget(self._rename_button)

        self._clear_button = QPushButton("Clear Label…", self)
        self._clear_button.setToolTip(
            "Removes the label from this user's conversations. The conversations stay — "
            "they simply stop being filtered under a name. To delete conversations, use "
            "the list on the left of the main window."
        )
        self._clear_button.clicked.connect(self._on_clear)
        buttons.addWidget(self._clear_button)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)

        self.refresh()

    # --- state ------------------------------------------------------------

    def refresh(self) -> None:
        counts = self._store.user_counts()
        names = {name for name, _count in counts}
        # The active name belongs in the list even with nothing under it
        # yet — otherwise picking a new one looks as though it did nothing.
        if self._active_user and self._active_user not in names:
            counts = sorted([*counts, (self._active_user, 0)], key=lambda row: row[0].casefold())
        self._counts = counts

        self._list.clear()
        for name, count in self._counts:
            plural = "" if count == 1 else "s"
            label = f"{name}  ({count} conversation{plural})" if count else f"{name}  (no conversations yet)"
            if name == self._active_user:
                label += "  — active"
            self._list.addItem(label)
        self._empty_label.setVisible(not self._counts)
        self._list.setVisible(bool(self._counts))
        self._update_buttons()

    def selected_user(self) -> str | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._counts):
            return None
        return self._counts[row][0]

    def _update_buttons(self) -> None:
        has_selection = self.selected_user() is not None
        self._rename_button.setEnabled(has_selection)
        self._clear_button.setEnabled(has_selection)

    # --- actions ----------------------------------------------------------

    def _on_new(self) -> None:
        """Start working under a name.

        Deliberately does not write anything: there is nowhere to write a
        user to. It sets the name active, and the next conversation is what
        makes it real — which is also why the refresh below shows it with
        "no conversations yet" rather than pretending something was saved.
        """
        name, ok = QInputDialog.getText(self, "New User", "Name (a person, or a project):")
        if not ok:
            return
        name = name.strip()
        if not name or name == self._active_user:
            return
        self._active_user = name
        self.new_active_user = name
        self.refresh()

    def _on_rename(self) -> None:
        current = self.selected_user()
        if current is None:
            return
        new_name, ok = QInputDialog.getText(self, "Rename or Merge", f"New name for {current!r}:", text=current)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == current:
            return
        existing = {name for name, _count in self._counts}
        if new_name in existing:
            # A merge is the point of this dialog, but it is irreversible
            # from here — the two histories become one and nothing records
            # which conversation came from which name.
            answer = QMessageBox.question(
                self,
                "Merge Users",
                f"{new_name!r} already exists. Move every conversation from {current!r} into it?\n\n"
                "This cannot be undone from here.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        moved = self._store.rename_user(current, new_name, timestamp=_now_iso())
        self.changed = True
        self.renamed_from, self.renamed_to = current, new_name
        if self._active_user == current:
            self._active_user = new_name
        self.refresh()
        QMessageBox.information(
            self, "Renamed", f"Moved {moved} conversation(s) from {current!r} to {new_name!r}."
        )

    def _on_clear(self) -> None:
        current = self.selected_user()
        if current is None:
            return
        answer = QMessageBox.question(
            self,
            "Clear Label",
            f"Remove the {current!r} label from their conversations?\n\n"
            "The conversations are kept — they stop being filtered under a name and "
            "become visible under every user. Nothing is deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cleared = self._store.rename_user(current, "", timestamp=_now_iso())
        self.changed = True
        self.renamed_from, self.renamed_to = current, ""
        if self._active_user == current:
            self._active_user = ""
        self.refresh()
        QMessageBox.information(self, "Label Cleared", f"Cleared the label from {cleared} conversation(s).")


__all__ = ["UsersDialog"]
