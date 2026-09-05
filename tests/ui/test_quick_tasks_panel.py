"""Tests for aida.ui.qt.quick_tasks_panel (B14)."""

from __future__ import annotations

from aida.ui.qt._qt import QMessageBox, QPoint
from aida.ui.qt.quick_tasks_panel import (
    MAX_QUICK_TASKS,
    QuickTaskData,
    QuickTaskEditDialog,
    QuickTasksPanel,
)


def _task(name: str = "Reduce data", text: str = "Reduce today's data.") -> QuickTaskData:
    return QuickTaskData(name=name, text=text)


def test_set_tasks_populates_list(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a"), _task("b")])
    assert panel.count == 2
    assert panel._list.count() == 2
    assert panel._list.item(0).text() == "a"


def test_tasks_returns_a_copy(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a")])
    tasks = panel.tasks()
    tasks.append(_task("b"))
    assert panel.count == 1  # the panel's own list is untouched


def test_double_click_emits_task_selected_with_the_text(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a", "text A"), _task("b", "text B")])
    selected = []
    panel.task_selected.connect(selected.append)
    panel._on_double_click(panel._list.item(1))
    assert selected == ["text B"]


# --- Add/Edit/Delete via the context menu -----------------------------


def test_add_via_dialog_appends_and_emits_tasks_changed(qapp, monkeypatch):
    panel = QuickTasksPanel()
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QuickTaskEditDialog.get_task",
        staticmethod(lambda *a, **kw: ("New Task", "Do the new thing.")),
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_add()
    assert panel.count == 1
    assert panel.tasks()[0] == QuickTaskData(name="New Task", text="Do the new thing.")
    assert changed == [[QuickTaskData(name="New Task", text="Do the new thing.")]]


def test_add_cancelled_does_not_emit(qapp, monkeypatch):
    panel = QuickTasksPanel()
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QuickTaskEditDialog.get_task",
        staticmethod(lambda *a, **kw: None),
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_add()
    assert panel.count == 0
    assert changed == []


def test_add_disabled_past_the_slot_cap(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task(f"task {i}") for i in range(MAX_QUICK_TASKS)])
    called = []
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QuickTaskEditDialog.get_task",
        staticmethod(lambda *a, **kw: called.append(True)),
    )
    panel._on_add()
    assert panel.count == MAX_QUICK_TASKS
    assert called == []  # the dialog is never even opened once the cap is hit


def test_context_menu_add_action_disabled_past_the_cap(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task(f"task {i}") for i in range(MAX_QUICK_TASKS)])
    menu = panel._build_context_menu(None)
    add_action = next(a for a in menu.actions() if a.text() == "Add…")
    assert add_action.isEnabled() is False


def test_context_menu_add_action_enabled_under_the_cap(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a")])
    menu = panel._build_context_menu(None)
    add_action = next(a for a in menu.actions() if a.text() == "Add…")
    assert add_action.isEnabled() is True


def test_context_menu_on_a_row_offers_edit_delete_and_add(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a")])
    menu = panel._build_context_menu(panel._list.item(0))
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == ["Edit…", "Delete…", "Add…"]


def test_context_menu_on_empty_space_offers_add_only(qapp):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a")])
    menu = panel._build_context_menu(None)
    labels = [action.text() for action in menu.actions()]
    assert labels == ["Add…"]


def test_edit_via_dialog_replaces_the_task(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("old name", "old text")])
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QuickTaskEditDialog.get_task",
        staticmethod(lambda *a, **kw: ("new name", "new text")),
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_edit(0)
    assert panel.tasks() == [QuickTaskData(name="new name", text="new text")]
    assert changed == [[QuickTaskData(name="new name", text="new text")]]


def test_edit_cancelled_leaves_the_task_unchanged(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("old name", "old text")])
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QuickTaskEditDialog.get_task",
        staticmethod(lambda *a, **kw: None),
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_edit(0)
    assert panel.tasks() == [_task("old name", "old text")]
    assert changed == []


def test_delete_confirmed_removes_the_task(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a"), _task("b")])
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_delete(0)
    assert panel.count == 1
    assert panel.tasks()[0].name == "b"
    assert changed == [[QuickTaskData(name="b", text="Reduce today's data.")]]


def test_delete_declined_leaves_tasks_unchanged(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a"), _task("b")])
    monkeypatch.setattr(
        "aida.ui.qt.quick_tasks_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    changed = []
    panel.tasks_changed.connect(changed.append)
    panel._on_delete(0)
    assert panel.count == 2
    assert changed == []


def test_context_menu_requested_does_not_raise_on_empty_space(qapp, monkeypatch):
    panel = QuickTasksPanel()
    panel.set_tasks([_task("a")])
    monkeypatch.setattr(QuickTasksPanel, "_popup_context_menu", lambda self, menu, pos: None)
    panel._on_context_menu_requested(QPoint(0, 5000))  # well below the single row


# --- QuickTaskEditDialog ------------------------------------------------


def test_edit_dialog_prefills_name_and_text(qapp):
    dialog = QuickTaskEditDialog(name="a name", text="some text")
    assert dialog.name() == "a name"
    assert dialog.text() == "some text"


def test_edit_dialog_get_task_returns_none_when_a_field_is_blank(qapp, monkeypatch):
    from aida.ui.qt._qt import QDialog

    monkeypatch.setattr(QuickTaskEditDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickTaskEditDialog, "name", lambda self: "")
    monkeypatch.setattr(QuickTaskEditDialog, "text", lambda self: "some text")
    assert QuickTaskEditDialog.get_task() is None
