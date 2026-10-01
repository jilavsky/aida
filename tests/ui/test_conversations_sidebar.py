"""Tests for aida.ui.qt.conversations_sidebar."""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import (
    QAbstractItemView,
    QDialog,
    QMessageBox,
    QPixmap,
    QStyleOptionViewItem,
    Qt,
)
from aida.ui.qt.conversations_sidebar import (
    ALL_USERS_LABEL,
    GROUP_HEADER_ROLE,
    MIN_SIDEBAR_WIDTH,
    NO_USER_LABEL,
    SUBTITLE_ROLE,
    TITLE_ROLE,
    UNDATED_GROUP,
    UNTITLED_LABEL,
    CleanupDialog,
    ConversationsSidebar,
    _date_group,
    _relative_when,
    _row_subtitle,
)


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


def _items(sidebar: ConversationsSidebar) -> list:
    """Every *conversation* row, in order.

    The list also holds non-selectable date-group headers now (see
    ``ConversationsSidebar._make_group_header``), so a test that wants
    "the second conversation" can no longer index ``_list`` directly.
    ``_ids_by_row`` carries ``None`` at each header's row, which is what
    this filters on.
    """
    return [
        sidebar._list.item(row)
        for row, conv_id in enumerate(sidebar._ids_by_row)
        if conv_id is not None
    ]


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
    sidebar._on_double_click(_items(sidebar)[1])
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
        "aida.ui.qt.conversations_sidebar.QMessageBox.question",
        lambda *a, **kw: called.append(True),
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
        "aida.ui.qt.conversations_sidebar.QInputDialog.getText",
        staticmethod(lambda *a, **kw: called.append(True)),
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
        "aida.ui.qt.conversations_sidebar.CleanupDialog.get_cutoff_days",
        staticmethod(lambda *a, **kw: 14),
    )
    cleaned = []
    sidebar.cleanup_requested.connect(cleaned.append)
    sidebar._on_cleanup_clicked()
    assert cleaned == [14]


def test_cleanup_button_cancelled_does_not_emit(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    monkeypatch.setattr(
        "aida.ui.qt.conversations_sidebar.CleanupDialog.get_cutoff_days",
        staticmethod(lambda *a, **kw: None),
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
    label = _items(sidebar)[0].text()
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
    assert "not-a-timestamp" in _items(sidebar)[0].text()


def test_search_filters_by_title_case_insensitively(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_summary("id1", title="USAXS beamtime notes"), _summary("id2", title="other chat")]
    )
    sidebar._search_edit.setText("usaxs")
    assert sidebar.count == 1
    assert sidebar.listed_conversation_ids() == ["id1"]


def test_search_filters_by_workspace_and_user(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [
            _summary("id1", title="first", workspace="usaxs-staff", user="Alice"),
            _summary("id2", title="second", workspace="manuals", user="Bob"),
        ]
    )

    sidebar._search_edit.setText("USAXS")
    assert sidebar.listed_conversation_ids() == ["id1"]

    sidebar._search_edit.setText("bob")
    assert sidebar.listed_conversation_ids() == ["id2"]


# --- planning/improvement_plan_2026-09.md §2: content search, not just
# title/workspace/user ------------------------------------------------------


def test_search_edit_emits_search_query_changed(qapp):
    sidebar = ConversationsSidebar()
    seen = []
    sidebar.search_query_changed.connect(seen.append)

    sidebar._search_edit.setText("sample X01")

    assert seen == ["sample X01"]


def test_content_match_ids_are_merged_into_the_visible_set(qapp):
    """A conversation whose *title* doesn't match but whose content search
    (run by MainWindow, handed back via set_content_matches) does is still
    shown — this is the whole point of the feature."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [
            _summary("id1", title="unrelated title"),
            _summary("id2", title="also unrelated"),
        ]
    )
    sidebar._search_edit.setText("sample X01")
    assert sidebar.listed_conversation_ids() == []  # no title/workspace/user match yet

    sidebar.set_content_matches({"id1"})

    assert sidebar.listed_conversation_ids() == ["id1"]


def test_content_matches_do_not_leak_into_an_unrelated_later_query(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="unrelated")])
    sidebar._search_edit.setText("sample X01")
    sidebar.set_content_matches({"id1"})
    assert sidebar.listed_conversation_ids() == ["id1"]

    # A fresh query with content_matches not yet updated for it must not
    # keep showing the previous query's matches.
    sidebar._search_edit.setText("something else entirely")
    assert sidebar.listed_conversation_ids() == []


def test_content_matches_cleared_with_none_stops_merging_stale_ids(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", title="unrelated")])
    sidebar._search_edit.setText("sample X01")
    sidebar.set_content_matches({"id1"})
    assert sidebar.listed_conversation_ids() == ["id1"]

    sidebar.set_content_matches(None)
    assert sidebar.listed_conversation_ids() == []


def test_content_matches_still_apply_alongside_a_title_match(qapp):
    """Both mechanisms contribute to the same visible set — a content match
    doesn't hide an ordinary title match, and vice versa."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [
            _summary("id1", title="sample X01 analysis"),
            _summary("id2", title="unrelated"),
        ]
    )
    sidebar._search_edit.setText("sample X01")
    sidebar.set_content_matches({"id2"})

    assert set(sidebar.listed_conversation_ids()) == {"id1", "id2"}


def test_user_filter_is_visible_only_when_user_labels_exist(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1", user="Alice"), _summary("id2", user="Bob")])
    assert not sidebar._user_filter.isHidden()
    assert [sidebar._user_filter.itemText(i) for i in range(sidebar._user_filter.count())] == [
        ALL_USERS_LABEL,
        "Alice",
        "Bob",
    ], "no (no user) entry when every conversation is labelled"

    sidebar.set_conversations([_summary("id3")])
    assert sidebar._user_filter.isHidden()


def test_user_filter_narrows_to_one_user_and_all_users_restores_everything(qapp):
    """Selecting a name shows only that name. Unlabelled conversations used
    to be included with every user — meant to keep a pre-existing history
    from vanishing, but since *all* of that history is unlabelled it made
    picking a name look like it did nothing. "(no user)" reaches them, and
    "All users" is the safety net."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_summary("alice", user="Alice"), _summary("bob", user="Bob"), _summary("legacy")]
    )

    sidebar._user_filter.setCurrentText("Alice")
    assert sidebar.listed_conversation_ids() == ["alice"]

    sidebar._user_filter.setCurrentText(NO_USER_LABEL)
    assert sidebar.listed_conversation_ids() == ["legacy"]

    sidebar._user_filter.setCurrentText(ALL_USERS_LABEL)
    assert sidebar.listed_conversation_ids() == ["alice", "bob", "legacy"]


def test_set_conversations_preserves_the_selected_user(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("alice", user="Alice"), _summary("bob", user="Bob")])
    sidebar._user_filter.setCurrentText("Bob")

    sidebar.set_conversations([_summary("bob-2", user="Bob"), _summary("alice-2", user="Alice")])

    assert sidebar._user_filter.currentText() == "Bob"
    assert sidebar.listed_conversation_ids() == ["bob-2"]


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
    assert sidebar.listed_conversation_ids() == ["id2"]


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
    _items(sidebar)[0].setSelected(True)
    _items(sidebar)[2].setSelected(True)
    assert sidebar.selected_conversation_ids() == ["id1", "id3"]


def test_selected_conversation_ids_empty_when_nothing_selected(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar._list.clearSelection()
    assert sidebar.selected_conversation_ids() == []


def test_delete_multiple_selected_emits_delete_many_requested(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    _items(sidebar)[0].setSelected(True)
    _items(sidebar)[1].setSelected(True)
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
    _items(sidebar)[0].setSelected(True)
    _items(sidebar)[1].setSelected(True)
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
    _items(sidebar)[0].setSelected(True)
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
    _items(sidebar)[0].setSelected(True)
    menu = sidebar._build_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == ["Resume", "Rename…", "Export…", "Move to User", "Delete…"]


def test_export_clicked_emits_export_requested(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1")])
    sidebar.select_row(0)
    exported = []
    sidebar.export_requested.connect(exported.append)
    sidebar._on_export_clicked()
    assert exported == ["id1"]


def test_context_menu_on_multiple_rows_offers_move_and_delete(qapp):
    """Resume and Rename stay single-selection; moving several chats to the
    right label at once is the common case, not the exception."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2")])
    _items(sidebar)[0].setSelected(True)
    _items(sidebar)[1].setSelected(True)
    menu = sidebar._build_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == ["Move to User", "Delete…"]


def test_right_clicking_an_unselected_row_selects_just_that_row(qapp, monkeypatch):
    """Right-clicking outside the current selection must replace it (same
    as a plain left click), not act on stale rows."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    _items(sidebar)[0].setSelected(True)
    monkeypatch.setattr(ConversationsSidebar, "_popup_context_menu", lambda self, menu, pos: None)

    pos = sidebar._list.visualItemRect(_items(sidebar)[2]).center()
    sidebar._on_context_menu_requested(pos)

    assert sidebar.selected_conversation_ids() == ["id3"]


def test_right_clicking_a_row_already_in_the_selection_keeps_the_whole_selection(qapp, monkeypatch):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("id1"), _summary("id2"), _summary("id3")])
    _items(sidebar)[0].setSelected(True)
    _items(sidebar)[1].setSelected(True)
    monkeypatch.setattr(ConversationsSidebar, "_popup_context_menu", lambda self, menu, pos: None)

    pos = sidebar._list.visualItemRect(_items(sidebar)[1]).center()
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
    sidebar.set_conversations(
        [
            _summary("id1", title="alpha"),
            _summary("id2", title="beta"),
            _summary("id3", title="alpha two"),
        ]
    )
    assert sidebar.count == 2
    assert sidebar._search_edit.text() == "alpha"


def _rows(sidebar) -> list[str]:
    return [item.text() for item in _items(sidebar)]


def _mixed() -> list:
    return [
        _summary("a", "Jan fits", user="Jan"),
        _summary("b", "Eva scans", user="Eva"),
        _summary("c", "Old chat", user=None),
    ]


def test_selecting_a_user_shows_only_that_users_conversations(qapp):
    """Bug report: creating a new user and selecting it still showed the
    same old chats. Unlabelled rows used to be included with every user —
    meant to stop a pre-existing history vanishing, but since *all* of that
    history is unlabelled it made picking a name look like a no-op."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())

    sidebar._user_filter.setCurrentText("Jan")
    rows = _rows(sidebar)
    assert len(rows) == 1
    assert "Jan fits" in rows[0]


def test_a_brand_new_user_shows_an_empty_list_not_everyone_elses(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed(), active_user="Brand New")
    assert _rows(sidebar) == []
    assert sidebar._user_filter.currentText() == "Brand New"


def test_the_filter_follows_a_switch_in_the_toolbar(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed(), active_user="Jan")
    assert sidebar._user_filter.currentText() == "Jan"

    sidebar.set_conversations(_mixed(), active_user="Eva")
    assert sidebar._user_filter.currentText() == "Eva"
    assert [r for r in _rows(sidebar) if "Eva scans" in r]


def test_an_explicit_choice_survives_an_ordinary_refresh(qapp):
    """Only a real switch overrides what was picked here — otherwise every
    sidebar refresh would yank the filter back."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed(), active_user="Jan")
    sidebar._user_filter.setCurrentText(ALL_USERS_LABEL)

    sidebar.set_conversations(_mixed(), active_user="Jan")
    assert sidebar._user_filter.currentText() == ALL_USERS_LABEL
    assert len(_rows(sidebar)) == 3


def test_unlabelled_conversations_are_reachable_on_their_own(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())

    sidebar._user_filter.setCurrentText(NO_USER_LABEL)
    rows = _rows(sidebar)
    assert len(rows) == 1
    assert "Old chat" in rows[0]


def test_no_user_entry_is_absent_when_everything_is_labelled(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([s for s in _mixed() if s.user])
    entries = [sidebar._user_filter.itemText(i) for i in range(sidebar._user_filter.count())]
    assert NO_USER_LABEL not in entries


def test_all_users_still_shows_everything(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed(), active_user="Jan")
    sidebar._user_filter.setCurrentText(ALL_USERS_LABEL)
    assert len(_rows(sidebar)) == 3


def test_context_menu_offers_move_to_user_for_one_and_for_many(qapp):
    """The wrong-name-selected mistake is exactly the kind that happens to
    a run of chats at once, so the submenu is offered for a multi-selection
    too — unlike Resume and Rename."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar.set_known_users(["Jan", "Eva"])

    sidebar.select_row(0)
    single = [a.text() for a in sidebar._build_context_menu().actions()]
    assert "Move to User" in single

    sidebar._list.selectAll()
    many = [a.text() for a in sidebar._build_context_menu().actions()]
    assert "Move to User" in many
    assert "Rename…" not in many


def test_move_to_user_submenu_lists_names_plus_no_user_and_new(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar.set_known_users(["Jan", "Eva"])
    sidebar.select_row(0)

    menu = sidebar._build_context_menu()
    submenu = next(a.menu() for a in menu.actions() if a.text() == "Move to User")
    entries = [a.text() for a in submenu.actions() if a.text()]
    assert entries[:2] == ["Jan", "Eva"]
    assert NO_USER_LABEL in entries
    assert "New user…" in entries


def test_choosing_a_name_emits_the_selected_ids(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar.set_known_users(["Jan"])
    sidebar.select_row(1)
    emitted = []
    sidebar.move_to_user_requested.connect(lambda ids, user: emitted.append((ids, user)))

    sidebar._on_move_to_user("Jan")
    assert emitted == [([sidebar.selected_conversation_ids()[0]], "Jan")]


def test_choosing_no_user_emits_an_empty_name(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar.select_row(0)
    emitted = []
    sidebar.move_to_user_requested.connect(lambda ids, user: emitted.append(user))

    sidebar._on_move_to_user("")
    assert emitted == [""]


def test_move_to_a_new_name_prompts_and_emits(qapp, monkeypatch):
    from aida.ui.qt._qt import QInputDialog

    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar.select_row(0)
    emitted = []
    sidebar.move_to_user_requested.connect(lambda ids, user: emitted.append(user))

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("  Carol  ", True))
    sidebar._on_move_to_new_user()
    assert emitted == ["Carol"]

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Carol", False))
    sidebar._on_move_to_new_user()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("   ", True))
    sidebar._on_move_to_new_user()
    assert emitted == ["Carol"], "cancelled or blank must emit nothing"


def test_move_to_user_with_nothing_selected_emits_nothing(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(_mixed())
    sidebar._list.setCurrentRow(-1)
    emitted = []
    sidebar.move_to_user_requested.connect(lambda ids, user: emitted.append(user))
    sidebar._on_move_to_user("Jan")
    assert emitted == []


def test_sidebar_can_be_resized_down_to_its_declared_minimum(qapp):
    """Bug report: "Left one is fixed width or hidden ... I cannot fit this
    on smaller screens."

    Nothing ever declared this column fixed-width — its four action buttons
    sat in a single row, and a QSplitter cannot shrink a pane below its
    layout's minimum, so that row's combined width *was* the floor. The
    guard is on the layout, not on any one widget: whatever this column
    grows to contain later, it must never ask the splitter for more than
    MIN_SIDEBAR_WIDTH.
    """
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_summary("c1", title="a conversation with a very long descriptive title")]
    )

    assert sidebar.minimumWidth() == MIN_SIDEBAR_WIDTH
    layout = sidebar.layout()
    assert layout is not None
    assert layout.minimumSize().width() <= MIN_SIDEBAR_WIDTH

    sidebar.resize(MIN_SIDEBAR_WIDTH, 600)
    qapp.processEvents()
    assert sidebar.width() == MIN_SIDEBAR_WIDTH


def test_narrow_rows_keep_their_full_label_as_a_tooltip(qapp):
    """The row text elides once the column is narrow, so the untruncated
    label has to stay reachable somewhere."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_summary("c1", title="quantitative USAXS of the aged sample")])

    item = _items(sidebar)[0]
    assert "quantitative USAXS of the aged sample" in item.toolTip()


# --- two-line rows under date groups --------------------------------------
#
# Bug report: "the display in the Conversation column is not very helpful.
# Could we make it into two lines — title first and date-time/workspace
# second? Or something more ergonomic, so user can easier find what he is
# looking for."


def _at(days_ago: float, *, hour: int = 14) -> str:
    """An ``updated_at`` that many days in the past, in local time — the
    rows are grouped and labelled relative to *now*, so the fixtures have
    to be too."""
    when = datetime.now().astimezone().replace(hour=hour, minute=3, second=0, microsecond=0)
    return (when - timedelta(days=days_ago)).isoformat()


def _dated(conv_id: str, title: str, iso: str, **kwargs) -> ConversationSummary:
    summary = _summary(conv_id, title, **kwargs)
    return dataclasses.replace(summary, updated_at=iso)


def _headers(sidebar: ConversationsSidebar) -> list[str]:
    return [
        sidebar._list.item(row).data(GROUP_HEADER_ROLE)
        for row, conv_id in enumerate(sidebar._ids_by_row)
        if conv_id is None
    ]


def test_each_row_carries_a_title_and_a_subtitle_the_delegate_paints(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_dated("c1", "Unified fit of PS latex", _at(0), workspace="usaxs", user="Jan")]
    )

    item = _items(sidebar)[0]
    assert item.data(TITLE_ROLE) == "Unified fit of PS latex"
    subtitle = item.data(SUBTITLE_ROLE)
    assert "usaxs" in subtitle
    assert "Jan" in subtitle
    # The title is on its own line, so it must not be repeated below it.
    assert "Unified fit" not in subtitle


def test_a_subtitle_drops_the_parts_a_conversation_does_not_have(qapp):
    """An install that uses no user labels must not carry a column of
    placeholder separators down the whole list."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", _at(0), workspace=None, user=None)])
    assert _items(sidebar)[0].data(SUBTITLE_ROLE) == _relative_when(_at(0))


def test_an_untitled_conversation_still_shows_something(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", None, _at(0))])
    assert _items(sidebar)[0].data(TITLE_ROLE) == UNTITLED_LABEL


def test_rows_are_grouped_under_date_headers_in_order(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [
            _dated("today", "today's work", _at(0)),
            _dated("yesterday", "yesterday's work", _at(1)),
            _dated("midweek", "earlier this week", _at(3)),
            _dated("ancient", "last season", _at(200)),
        ]
    )

    assert _headers(sidebar)[:3] == ["Today", "Yesterday", "Previous 7 days"]
    assert len(_headers(sidebar)) == 4  # the fourth is a month name
    assert sidebar.count == 4  # headers are not conversations


def test_consecutive_conversations_in_one_band_share_a_single_header(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated(f"c{i}", f"chat {i}", _at(0, hour=10 + i)) for i in range(4)])
    assert _headers(sidebar) == ["Today"]


def test_a_header_cannot_be_selected_or_acted_on(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", _at(0))])

    header_row = sidebar._ids_by_row.index(None)
    header = sidebar._list.item(header_row)
    assert header.flags() == Qt.ItemFlag.NoItemFlags

    # Even reached directly (a stray setCurrentRow, selectAll), it is not
    # a conversation and no action can be aimed at it.
    sidebar._list.setCurrentRow(header_row)
    assert sidebar.selected_conversation_id() is None
    sidebar._list.selectAll()
    assert sidebar.selected_conversation_ids() == ["c1"]


def test_double_clicking_a_header_resumes_nothing(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", _at(0))])
    resumed = []
    sidebar.resume_requested.connect(resumed.append)

    header_row = sidebar._ids_by_row.index(None)
    sidebar._on_double_click(sidebar._list.item(header_row))
    assert resumed == []


def test_select_row_counts_conversations_not_view_rows(qapp):
    """Headers come and go with the filter, so an index into the *list*
    would mean something different every refresh."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_dated("today", "today's work", _at(0)), _dated("old", "older work", _at(3))]
    )
    sidebar.select_row(1)
    assert sidebar.selected_conversation_id() == "old"


def test_headers_follow_the_filtered_set_not_the_whole_history(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_dated("today", "alpha today", _at(0)), _dated("old", "beta earlier", _at(3))]
    )
    sidebar._search_edit.setText("alpha")
    assert _headers(sidebar) == ["Today"]
    assert sidebar.count == 1


# --- how a timestamp reads ------------------------------------------------


def test_relative_when_says_the_time_for_today_and_names_the_day_this_week():
    now = datetime(2026, 10, 1, 17, 0).astimezone()
    assert _relative_when(datetime(2026, 10, 1, 9, 3).astimezone().isoformat(), now=now) == "09:03"
    assert (
        _relative_when(datetime(2026, 9, 30, 17, 44).astimezone().isoformat(), now=now)
        == "Yesterday 17:44"
    )
    assert (
        _relative_when(datetime(2026, 9, 26, 11, 2).astimezone().isoformat(), now=now)
        == "Sat 11:02"
    )


def test_relative_when_adds_the_year_only_once_it_stops_being_obvious():
    now = datetime(2026, 10, 1, 17, 0).astimezone()
    assert _relative_when(datetime(2026, 3, 12, 9, 0).astimezone().isoformat(), now=now) == "Mar 12"
    assert (
        _relative_when(datetime(2025, 9, 12, 9, 0).astimezone().isoformat(), now=now)
        == "Sep 12, 2025"
    )


def test_anything_older_than_a_week_groups_by_month():
    now = datetime(2026, 10, 1, 17, 0).astimezone()
    older = datetime(2026, 9, 12, 9, 0).astimezone().isoformat()
    assert _date_group(older, now=now) == "September 2026"


def test_an_unparseable_timestamp_is_shown_and_grouped_rather_than_crashing(qapp):
    """A hand-edited or foreign DB row must never take the sidebar down —
    the same rule the single-line label already followed."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", "not-a-timestamp")])

    assert _headers(sidebar) == [UNDATED_GROUP]
    assert "not-a-timestamp" in _items(sidebar)[0].data(SUBTITLE_ROLE)


def test_the_delegate_actually_paints_both_row_kinds(qapp):
    """A smoke test with teeth: nothing above this forces a real paint, so
    a mistake inside _ConversationRowDelegate.paint (a bad enum, a wrong
    palette role, an unsaved painter) would go unnoticed until the app
    ran. Rendering the viewport exercises both a selected conversation
    row and a group header."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_dated("today", "Unified fit of PS latex", _at(0)), _dated("old", "older work", _at(3))]
    )
    sidebar.resize(260, 400)
    sidebar.select_row(0)

    pixmap = QPixmap(sidebar._list.viewport().size())
    pixmap.fill()
    sidebar._list.viewport().render(pixmap)
    assert not pixmap.isNull()


def test_rows_report_no_preferred_width_so_the_column_can_still_shrink(qapp):
    """MIN_SIDEBAR_WIDTH only holds if nothing inside the list asks to be
    wider — a sizeHint carrying the text's natural width would re-impose
    exactly the floor that constant exists to remove."""
    sidebar = ConversationsSidebar()
    sidebar.set_conversations(
        [_dated("c1", "a very long conversation title that would never fit", _at(0))]
    )
    option = QStyleOptionViewItem()
    option.initFrom(sidebar._list)
    index = sidebar._list.indexFromItem(_items(sidebar)[0])
    hint = sidebar._row_delegate.sizeHint(option, index)
    assert hint.width() == 0
    assert hint.height() > 0


def test_a_header_row_is_shorter_than_a_conversation_row(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", _at(0))])
    option = QStyleOptionViewItem()
    option.initFrom(sidebar._list)

    header_row = sidebar._ids_by_row.index(None)
    header_hint = sidebar._row_delegate.sizeHint(
        option, sidebar._list.indexFromItem(sidebar._list.item(header_row))
    )
    row_hint = sidebar._row_delegate.sizeHint(
        option, sidebar._list.indexFromItem(_items(sidebar)[0])
    )
    assert 0 < header_hint.height() < row_hint.height()


def test_a_row_under_the_yesterday_heading_does_not_say_yesterday_again(qapp):
    sidebar = ConversationsSidebar()
    sidebar.set_conversations([_dated("c1", "chat", _at(1, hour=17))])

    assert _headers(sidebar) == ["Yesterday"]
    subtitle = _items(sidebar)[0].data(SUBTITLE_ROLE)
    assert subtitle.startswith("17:03")
    assert "Yesterday" not in subtitle


def test_the_standalone_form_still_names_the_day(qapp):
    """Without a heading above it there is nothing else saying which day
    it was, so the long form is the right one."""
    summary = _dated("c1", "chat", _at(1, hour=17))
    assert _row_subtitle(summary).startswith("Yesterday 17:03")
