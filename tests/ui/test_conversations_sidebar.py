"""Tests for aida.ui.qt.conversations_sidebar."""

from __future__ import annotations

import re

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import QDialog, QMessageBox
from aida.ui.qt.conversations_sidebar import CleanupDialog, ConversationsSidebar


def _summary(conv_id: str, title: str = "chat", workspace: str | None = "use-pyirena") -> ConversationSummary:
    return ConversationSummary(
        id=conv_id,
        title=title,
        workspace_name=workspace,
        profile_name="p1",
        sidecar_dirname="figures",
        created_at="2026-08-19T00:00:00",
        updated_at="2026-08-19T00:00:00",
        record_path=None,
        message_count=2,
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
