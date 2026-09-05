"""Tests for aida.ui.qt.conversations_sidebar."""

from __future__ import annotations

import re

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import QAbstractItemView, QDialog, QMessageBox
from aida.ui.qt.conversations_sidebar import ALL_USERS_LABEL, CleanupDialog, ConversationsSidebar


def _summary(
    conv_id: str,
    title: str = "chat",
    workspace: str | None = "use-pyirena",
    *,
    message_count: int = 2,
    user: str | None = None,
) -> ConversationSummary:
    return ConversationSummary(
        id=conv_id,
        title=title,
        workspace_name=workspace,
        profile_name="p1",
        sidecar_dirname="figures",
        created_at="2026-08-19T00:00:00",
        updated_at="2026-08-19T00:00:00",
        record_path=None,
        message_count=message_count,
        user=user,
    )


def test_set_conversations_populates_list(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2", title="other")])
    assert sidebar.count == 2


def test_select_row_and_selected_conversation_id(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    sidebar.select_row(1)
    assert sidebar.selected_conversation_id() == "id2"


def test_no_selection_returns_none(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar._list.setCurrentRow(-1)
    assert sidebar.selected_conversation_id() is None


def test_resume_button_emits_resume_requested(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar.select_row(0)
    resumed = []
    sidebar.resume_requested.connect(resumed.append)
    sidebar._resume_button.click()
    assert resumed == ["id1"]


def test_double_click_emits_resume_requested(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    resumed = []
    sidebar.resume_requested.connect(resumed.append)
    sidebar._on_double_click(sidebar._list.item(1))
    assert resumed == ["id2"]


def test_delete_confirmed_emits_delete_requested(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar.select_row(0)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    deleted = []
    sidebar.delete_requested.connect(deleted.append)
    sidebar._on_delete_clicked()
    assert deleted == ["id1"]


def test_delete_declined_does_not_emit(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar.select_row(0)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    deleted = []
    sidebar.delete_requested.connect(deleted.append)
    sidebar._on_delete_clicked()
    assert deleted == []


def test_delete_with_no_selection_is_a_noop(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([])
    called = []
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question", lambda *a, **kw: called.append(True)
    )
    sidebar._on_delete_clicked()
    assert called == []


def test_rename_confirmed_emits_rename_requested_with_new_title(qapp, monkeypatch):
    """Bug report: "Can we have the chat list in the history column have
    some kind of names? ... these date/times are not very convenient to
    use.\""""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="old title")])
    sidebar.select_row(0)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QInputDialog.getText",
        staticmethod(lambda *a, **kw: ("USAXS beamtime notes", True)),
    )
    renamed = []
    sidebar.rename_requested.connect(lambda conv_id, title: renamed.append((conv_id, title)))
    sidebar._on_rename_clicked()
    assert renamed == [("id1", "USAXS beamtime notes")]


def test_rename_cancelled_does_not_emit(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="old title")])
    sidebar.select_row(0)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QInputDialog.getText",
        staticmethod(lambda *a, **kw: ("new title", False)),
    )
    renamed = []
    sidebar.rename_requested.connect(lambda conv_id, title: renamed.append((conv_id, title)))
    sidebar._on_rename_clicked()
    assert renamed == []


def test_rename_with_blank_title_does_not_emit(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="old title")])
    sidebar.select_row(0)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QInputDialog.getText",
        staticmethod(lambda *a, **kw: ("   ", True)),
    )
    renamed = []
    sidebar.rename_requested.connect(lambda conv_id, title: renamed.append((conv_id, title)))
    sidebar._on_rename_clicked()
    assert renamed == []


def test_rename_with_no_selection_is_a_noop(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([])
    called = []
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QInputDialog.getText", staticmethod(lambda *a, **kw: called.append(True))
    )
    sidebar._on_rename_clicked()
    assert called == []


def test_cleanup_dialog_days_default_and_getter(qapp):
    dialog = CleanupDialog(default_days=45)
    assert dialog.days() == 45
    dialog._days_spin.setValue(10)
    assert dialog.days() == 10


def test_cleanup_dialog_accept_via_button_box(qapp):
    dialog = CleanupDialog(default_days=7)
    dialog.accept()  # simulate OK without a real exec() loop
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_cleanup_button_emits_cleanup_requested(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.CleanupDialog.get_cutoff_days", staticmethod(lambda *a, **kw: 14)
    )
    cleaned = []
    sidebar.cleanup_requested.connect(cleaned.append)
    sidebar._on_cleanup_clicked()
    assert cleaned == [14]


def test_cleanup_button_cancelled_does_not_emit(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.CleanupDialog.get_cutoff_days", staticmethod(lambda *a, **kw: None)
    )
    cleaned = []
    sidebar.cleanup_requested.connect(cleaned.append)
    sidebar._on_cleanup_clicked()
    assert cleaned == []


# --- U5: local short date/time + a live title filter ------------------------


def test_row_label_shows_local_short_date_time_not_the_raw_iso_string(qapp):
    """Bug report: "these date/times are not very convenient to use" — the
    row used to show the raw UTC ISO-8601 updated_at string verbatim."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="analysis")])
    label = sidebar._list.item(0).text()
    assert "2026-08-19T00:00:00" not in label
    assert "[use-pyirena]" in label
    assert "analysis" in label
    # "Aug 19 HH:MM" shape — exact minute depends on the viewer's local
    # timezone offset from the stored UTC timestamp, so only the shape is
    # asserted, not a specific clock time.
    assert re.search(r"^[A-Z][a-z]{2} \d{2} \d{2}:\d{2}", label)


def test_row_label_falls_back_to_the_raw_string_for_unparseable_timestamps(qapp):
    sidebar = ConversationsSidebar()
    bad = _summary("id1")
    bad.updated_at = "not-a-timestamp"
    sidebar.set_conversations([bad])
    assert "not-a-timestamp" in sidebar._list.item(0).text()


def test_search_filters_by_title_case_insensitively(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="USAXS beamtime notes"), _summary("id2", title="other chat")])
    sidebar._search_edit.setText("usaxs")
    assert sidebar.count == 1
    assert sidebar._ids_by_row == ["id1"]


def test_search_filters_by_workspace_and_user(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [
            _summary("id1", title="first", workspace="usaxs-staff", user="Alice"),
            _summary("id2", title="second", workspace="manuals", user="Bob"),
        ]
    )

    sidebar._search_edit.setText("USAXS")
    assert sidebar._ids_by_row == ["id1"]

    sidebar._search_edit.setText("bob")
    assert sidebar._ids_by_row == ["id2"]


def test_user_filter_is_visible_only_when_user_labels_exist(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", user="Alice"), _summary("id2", user="Bob")])
    assert not sidebar._user_filter.isHidden()
    assert [sidebar._user_filter.itemText(i) for i in range(sidebar._user_filter.count())] == [
        ALL_USERS_LABEL,
        "Alice",
        "Bob",
    ]

    sidebar.set_conversations([_summary("id3")])
    assert sidebar._user_filter.isHidden()


def test_user_filter_keeps_unowned_conversations_visible_and_all_users_restores_everything(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_summary("alice", user="Alice"), _summary("bob", user="Bob"), _summary("legacy")]
    )

    sidebar._user_filter.setCurrentText("Alice")
    assert sidebar._ids_by_row == ["alice", "legacy"]

    sidebar._user_filter.setCurrentText(ALL_USERS_LABEL)
    assert sidebar._ids_by_row == ["alice", "bob", "legacy"]


def test_set_conversations_preserves_the_selected_user(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("alice", user="Alice"), _summary("bob", user="Bob")])
    sidebar._user_filter.setCurrentText("Bob")

    sidebar.set_conversations([_summary("bob-2", user="Bob"), _summary("alice-2", user="Alice")])

    assert sidebar._user_filter.currentText() == "Bob"
    assert sidebar._ids_by_row == ["bob-2"]


def test_search_with_no_matches_shows_an_empty_list(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="alpha"), _summary("id2", title="beta")])
    sidebar._search_edit.setText("no such conversation")
    assert sidebar.count == 0


def test_clearing_search_restores_every_conversation(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="alpha"), _summary("id2", title="beta")])
    sidebar._search_edit.setText("alpha")
    assert sidebar.count == 1
    sidebar._search_edit.setText("")
    assert sidebar.count == 2


# --- empty conversations never show (bug report: "Let's not add in this
# list ... conversations which have no messages in them") ------------------


def test_set_conversations_hides_conversations_with_no_messages(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", message_count=0), _summary("id2", message_count=1)])
    assert sidebar.count == 1
    assert sidebar._ids_by_row == ["id2"]


def test_set_conversations_with_only_empty_conversations_shows_nothing(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", message_count=0)])
    assert sidebar.count == 0


# --- multi-select (bug report: "Enable multiple file selection ... shift
# click to select range and ctrl/cmd click ... useful for deleting multiple
# chats") ---------------------------------------------------------------


def test_selection_mode_is_extended(qapp):
    """The actual shift-range/ctrl-toggle mouse behavior is Qt's own
    ExtendedSelection implementation — this just pins down that the widget
    is actually configured for it."""
    sidebar = ConversationsSidebar()
    assert sidebar._list.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection


def test_selected_conversation_ids_returns_every_selected_row(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    sidebar._list.item(0).setSelected(True)
    sidebar._list.item(2).setSelected(True)
    assert sidebar.selected_conversation_ids() == ["id1", "id3"]


def test_selected_conversation_ids_empty_when_nothing_selected(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar._list.clearSelection()
    assert sidebar.selected_conversation_ids() == []


def test_delete_multiple_selected_emits_delete_many_requested(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    sidebar._list.item(0).setSelected(True)
    sidebar._list.item(1).setSelected(True)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    single_deleted = []
    many_deleted = []
    sidebar.delete_requested.connect(single_deleted.append)
    sidebar.delete_many_requested.connect(many_deleted.append)
    sidebar._on_delete_clicked()
    assert single_deleted == []
    assert many_deleted == [["id1", "id2"]]


def test_delete_multiple_declined_emits_nothing(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    sidebar._list.item(0).setSelected(True)
    sidebar._list.item(1).setSelected(True)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    many_deleted = []
    sidebar.delete_many_requested.connect(many_deleted.append)
    sidebar._on_delete_clicked()
    assert many_deleted == []


def test_delete_single_selection_still_emits_the_singular_signal(qapp, monkeypatch):
    """Backward-compat check: a plain single selection must still use
    delete_requested, not the new bulk signal."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    sidebar._list.item(0).setSelected(True)
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    single_deleted = []
    many_deleted = []
    sidebar.delete_requested.connect(single_deleted.append)
    sidebar.delete_many_requested.connect(many_deleted.append)
    sidebar._on_delete_clicked()
    assert single_deleted == ["id1"]
    assert many_deleted == []


# --- right-click context menu (bug report: "Add meaningful (one
# conversation action) button functions to the right click (rename,
# resume, delete)") ----------------------------------------------------


def test_context_menu_on_a_single_row_offers_resume_rename_delete(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar._list.item(0).setSelected(True)
    menu = sidebar._build_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == ["Resume", "Rename…", "Delete…"]


def test_context_menu_on_multiple_rows_offers_delete_only(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    sidebar._list.item(0).setSelected(True)
    sidebar._list.item(1).setSelected(True)
    menu = sidebar._build_context_menu()
    labels = [action.text() for action in menu.actions()]
    assert labels == ["Delete…"]


def test_right_clicking_an_unselected_row_selects_just_that_row(qapp, monkeypatch):
    """Right-clicking outside the current selection must replace it (same
    as a plain left click), not act on stale rows."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    sidebar._list.item(0).setSelected(True)
    monkeypatch.setattr(ConversationsSidebar, "_popup_context_menu", lambda self, menu, pos: None)

    pos = sidebar._list.visualItemRect(sidebar._list.item(2)).center()
    sidebar._on_context_menu_requested(pos)

    assert sidebar.selected_conversation_ids() == ["id3"]


def test_right_clicking_a_row_already_in_the_selection_keeps_the_whole_selection(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    sidebar._list.item(0).setSelected(True)
    sidebar._list.item(1).setSelected(True)
    monkeypatch.setattr(ConversationsSidebar, "_popup_context_menu", lambda self, menu, pos: None)

    pos = sidebar._list.visualItemRect(sidebar._list.item(1)).center()
    sidebar._on_context_menu_requested(pos)

    assert sidebar.selected_conversation_ids() == ["id1", "id2"]


def test_right_clicking_empty_space_does_not_raise(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    monkeypatch.setattr(ConversationsSidebar, "_popup_context_menu", lambda self, menu, pos: None)
    from aida.ui.qt._qt import QPoint

    sidebar._on_context_menu_requested(QPoint(0, 5000))  # well below the single row


def test_refreshing_conversations_preserves_an_active_filter(qapp):
    """set_conversations is called again on every resume/delete/rename/
    cleanup — it must re-apply whatever the user already typed rather than
    silently clearing the search box."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="alpha"), _summary("id2", title="beta")])
    sidebar._search_edit.setText("alpha")
    assert sidebar.count == 1

    # A refresh with a fresh set of summaries (e.g. after a rename) — the
    # filter text itself is untouched, so it must still apply.
    sidebar.set_conversations([_summary("id1", title="alpha"), _summary("id2", title="beta"), _summary("id3", title="alpha two")])
    assert sidebar.count == 2
    assert sidebar._search_edit.text() == "alpha"
