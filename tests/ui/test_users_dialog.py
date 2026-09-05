"""Tests for aida.ui.qt.users_dialog.UsersDialog.

The dialog exists because the toolbar's user box is free text: nothing
validates a typed name, so a typo splits one person's history and nothing
else in the app can put it back together.
"""

from __future__ import annotations

from pathlib import Path

from aida.persistence.store import ConversationStore
from aida.providers.base import Message
from aida.ui.qt._qt import QInputDialog, QMessageBox
from aida.ui.qt.users_dialog import UsersDialog


def _store(tmp_path: Path) -> ConversationStore:
    store = ConversationStore(tmp_path / "aida.db")
    for user, title in [("Jan", "fits"), ("Jam", "typo"), ("Eva", "scans"), (None, "old")]:
        conv = store.create_conversation(timestamp="2026-01-01", title=title, user=user)
        store.append_message(conv, Message(role="user", content="hi"), timestamp="2026-01-02")
    return store


def test_new_user_makes_a_name_active_and_shows_it_immediately(qapp, tmp_path: Path, monkeypatch):
    """The dead end this fixes: with no names in use, the dialog listed
    nothing and greyed both buttons out, so a dialog called "Manage Users"
    could not get you a user. Nothing is written — there is nowhere to
    write a user to — but the name becomes active and is shown as such, so
    it does not look as though the button did nothing."""
    store = ConversationStore(tmp_path / "aida.db")
    try:
        dialog = UsersDialog(store)
        assert dialog._list.count() == 0
        assert dialog._empty_label.isVisibleTo(dialog)

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jan", True))
        dialog._on_new()

        assert dialog.new_active_user == "Jan"
        rows = [dialog._list.item(i).text() for i in range(dialog._list.count())]
        assert rows == ["Jan  (no conversations yet)  — active"]
        assert store.user_counts() == [], "nothing is written until a conversation uses it"
    finally:
        store.close()


def test_new_user_cancelled_or_blank_does_nothing(qapp, tmp_path: Path, monkeypatch):
    store = ConversationStore(tmp_path / "aida.db")
    try:
        dialog = UsersDialog(store)
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jan", False))
        dialog._on_new()
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("   ", True))
        dialog._on_new()
        assert dialog.new_active_user is None
    finally:
        store.close()


def test_the_active_name_is_marked_even_before_it_has_conversations(qapp, tmp_path: Path):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store, active_user="Brand New")
        rows = [dialog._list.item(i).text() for i in range(dialog._list.count())]
        assert "Brand New  (no conversations yet)  — active" in rows
        assert len(rows) == 4
    finally:
        store.close()


def test_lists_every_label_with_its_count(qapp, tmp_path: Path):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        rows = [dialog._list.item(i).text() for i in range(dialog._list.count())]
        assert any(row.startswith("Eva") for row in rows)
        assert any(row.startswith("Jan") for row in rows)
        # Unlabelled conversations are not a user and must not appear.
        assert len(rows) == 3
        assert "1 conversation)" in rows[0]
    finally:
        store.close()


def test_buttons_are_disabled_with_nothing_selected_but_new_user_is_not(qapp, tmp_path: Path):
    """New User… must stay enabled with nothing selected — it is the only
    way out of an empty dialog."""
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow(-1)
        assert not dialog._rename_button.isEnabled()
        assert not dialog._clear_button.isEnabled()
        assert dialog._new_button.isEnabled()
    finally:
        store.close()


def test_renaming_the_active_name_keeps_the_dialog_in_step(qapp, tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store, active_user="Eva")
        dialog._list.setCurrentRow([n for n, _c in dialog._counts].index("Eva"))
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Eva Novak", True))
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        dialog._on_rename()

        rows = [dialog._list.item(i).text() for i in range(dialog._list.count())]
        assert any(row.startswith("Eva Novak") and row.endswith("— active") for row in rows)
    finally:
        store.close()


def test_rename_moves_the_conversations(qapp, tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow([n for n, _c in dialog._counts].index("Eva"))
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Eva Novak", True))
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        dialog._on_rename()

        assert dialog.changed is True
        assert ("Eva Novak", 1) in store.user_counts()
        assert "Eva" not in dict(store.user_counts())
    finally:
        store.close()


def test_renaming_onto_an_existing_name_asks_before_merging(qapp, tmp_path: Path, monkeypatch):
    """A merge is what fixing a typo means — but it is irreversible from
    here, so it is confirmed rather than assumed."""
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow([n for n, _c in dialog._counts].index("Jam"))
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jan", True))
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No,
        )

        dialog._on_rename()

        assert asked, "a merge must be confirmed"
        assert dialog.changed is False
        assert dict(store.user_counts())["Jan"] == 1, "declining must change nothing"
    finally:
        store.close()


def test_confirmed_merge_combines_the_histories(qapp, tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow([n for n, _c in dialog._counts].index("Jam"))
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jan", True))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        dialog._on_rename()

        assert dict(store.user_counts())["Jan"] == 2
        assert "Jam" not in dict(store.user_counts())
    finally:
        store.close()


def test_clearing_a_label_keeps_the_conversations(qapp, tmp_path: Path, monkeypatch):
    """Removing a *label* is not deleting work — that is the sidebar's job
    and must not be duplicated here."""
    store = _store(tmp_path)
    try:
        before = len(store.list_conversations())
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow([n for n, _c in dialog._counts].index("Eva"))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        dialog._on_clear()

        assert "Eva" not in dict(store.user_counts())
        assert len(store.list_conversations()) == before
    finally:
        store.close()


def test_a_cancelled_rename_changes_nothing(qapp, tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    try:
        dialog = UsersDialog(store)
        dialog._list.setCurrentRow(0)
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("whatever", False))

        dialog._on_rename()

        assert dialog.changed is False
        assert len(store.user_counts()) == 3
    finally:
        store.close()
