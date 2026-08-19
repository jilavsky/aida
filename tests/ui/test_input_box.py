"""Tests for aida.ui.qt.input_box.InputBox."""

from __future__ import annotations

from aida.ui.qt._qt import Qt
from aida.ui.qt.input_box import InputBox


def test_typing_and_button_click_sends_and_clears(qapp):
    box = InputBox()
    box.set_text("hello there")

    sent = []
    box.send_requested.connect(sent.append)
    box._send_button.click()

    assert sent == ["hello there"]
    assert box.text() == ""


def test_empty_text_does_not_send(qapp):
    box = InputBox()
    sent = []
    box.send_requested.connect(sent.append)
    box._send_button.click()
    assert sent == []


def test_whitespace_only_does_not_send(qapp):
    box = InputBox()
    box.set_text("   \n  ")
    sent = []
    box.send_requested.connect(sent.append)
    box._send_button.click()
    assert sent == []


def test_enter_key_submits(qapp):
    from PySide6.QtTest import QTest

    box = InputBox()
    box._text_edit.setPlainText("hi")
    sent = []
    box.send_requested.connect(sent.append)

    QTest.keyClick(box._text_edit, Qt.Key.Key_Return)

    assert sent == ["hi"]


def test_shift_enter_inserts_newline_instead_of_sending(qapp):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest

    box = InputBox()
    box._text_edit.setPlainText("line1")
    box._text_edit.moveCursor(QTextCursor.MoveOperation.End)  # setPlainText leaves the cursor at position 0
    sent = []
    box.send_requested.connect(sent.append)

    QTest.keyClick(box._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)

    assert sent == []
    assert "\n" in box.text()
    assert box.text().startswith("line1")


def test_set_busy_disables_input_and_relabels_button(qapp):
    box = InputBox()
    assert not box.is_busy
    assert box._send_button.text() == "Send"

    box.set_busy(True)
    assert box.is_busy
    assert box._send_button.text() == "Stop"
    assert not box._text_edit.isEnabled()

    box.set_busy(False)
    assert not box.is_busy
    assert box._send_button.text() == "Send"
    assert box._text_edit.isEnabled()


def test_button_click_while_busy_cancels_instead_of_sending(qapp):
    box = InputBox()
    box.set_text("hello")
    box.set_busy(True)

    cancelled = []
    sent = []
    box.cancel_requested.connect(lambda: cancelled.append(True))
    box.send_requested.connect(sent.append)

    box._send_button.click()

    assert cancelled == [True]
    assert sent == []


def test_enter_while_busy_does_not_send(qapp):
    box = InputBox()
    box.set_busy(True)
    # Text edit is disabled while busy, but exercise _on_submit's own guard
    # directly (the busy check), independent of whether the disabled widget
    # would even deliver the key event in a real UI.
    sent = []
    box.send_requested.connect(sent.append)
    box._text_edit.setEnabled(True)  # bypass the disabled-widget short-circuit for this check
    box._text_edit.setPlainText("hello")
    box._on_submit()
    assert sent == []
