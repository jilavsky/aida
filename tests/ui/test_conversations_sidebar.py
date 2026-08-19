"""Tests for aida.ui.qt.conversations_sidebar."""

from __future__ import annotations

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
