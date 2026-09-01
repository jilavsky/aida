"""Tests for aida.ui.qt.notes_panel.NotesPanel — the per-workspace notepad.

Same widget-level, no-bridge pattern as tests/ui/test_quick_tasks_panel.py:
the panel holds text and emits a signal; MainWindow is what persists it
(tests/ui/test_main_window.py covers that half).
"""

from __future__ import annotations

from aida.ui.qt.notes_panel import NotesPanel
from tests.ui._qt_test_utils import pump_until


def test_the_debounce_timer_actually_saves(qapp):
    """The normal path: type, stop typing, and let the timer fire on its own.

    Regression guard for the bug this panel shipped with — flush() gated on
    ``_save_timer.isActive()``, which is already False inside the timer's
    own timeout handler, so nothing was ever saved unless something called
    flush() by hand while the timer was still armed. Every other test in
    this file did exactly that, which is why they all passed on a panel
    that could not save at all. This one drives the real signal.
    """
    panel = NotesPanel()
    panel._save_timer.setInterval(0)  # same code path, no waiting
    saved = []
    panel.notes_changed.connect(saved.append)

    panel._edit.setPlainText("re-fit run 43 tomorrow")

    assert pump_until(qapp, lambda: bool(saved)), "the debounced save never fired"
    assert saved == ["re-fit run 43 tomorrow"]
    assert not panel.has_unsaved_edit
    assert panel._status.text() == "Saved"


def test_status_says_saving_then_saved(qapp):
    """The user-visible half of the same bug: the label sat on "Saving…"
    forever."""
    panel = NotesPanel()
    panel._save_timer.setInterval(0)

    panel._edit.setPlainText("something")
    assert panel._status.text() == "Saving…"

    assert pump_until(qapp, lambda: panel._status.text() == "Saved")


def test_typing_emits_notes_changed_once_the_debounce_fires(qapp):
    panel = NotesPanel()
    saved = []
    panel.notes_changed.connect(saved.append)

    panel._edit.setPlainText("check the Guinier fit on run 42")

    assert saved == []  # not yet: a keystroke is not a save
    assert panel.has_unsaved_edit
    panel.flush()
    assert saved == ["check the Guinier fit on run 42"]


def test_flush_is_a_no_op_with_nothing_pending(qapp):
    """Callers (window close, workspace switch) shouldn't have to know
    whether an edit is pending."""
    panel = NotesPanel()
    saved = []
    panel.notes_changed.connect(saved.append)

    panel.flush()
    panel.flush()

    assert saved == []


def test_loading_saved_notes_does_not_look_like_an_edit(qapp):
    """Populating the box from the workspace must not immediately write it
    straight back — every workspace switch would be a config write."""
    panel = NotesPanel()
    saved = []
    panel.notes_changed.connect(saved.append)

    panel.set_notes("notes from last week")

    assert panel.notes() == "notes from last week"
    assert not panel.has_unsaved_edit
    assert saved == []


def test_switching_workspaces_flushes_the_outgoing_notes_first(qapp):
    """Otherwise a sentence typed just before switching would be written
    into the *incoming* workspace."""
    panel = NotesPanel()
    saved = []
    panel.notes_changed.connect(saved.append)
    panel.set_notes("workspace A notes")

    panel._edit.setPlainText("workspace A notes, plus one more thought")
    panel.set_notes("workspace B notes")  # the switch

    assert saved == ["workspace A notes, plus one more thought"]
    assert panel.notes() == "workspace B notes"


def test_further_typing_restarts_the_quiet_period(qapp):
    panel = NotesPanel()
    saved = []
    panel.notes_changed.connect(saved.append)

    panel._edit.setPlainText("first")
    panel._edit.setPlainText("first, second")
    panel.flush()

    assert saved == ["first, second"]  # one save, not one per keystroke
