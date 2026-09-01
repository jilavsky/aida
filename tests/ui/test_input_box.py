"""Tests for aida.ui.qt.input_box.InputBox."""

from __future__ import annotations

from aida.ui.qt._qt import Qt
from aida.ui.qt.input_box import InputBox


class _FakeDropEvent:
    """Duck-typed stand-in for QDropEvent: InputBox.dropEvent only calls
    ``.mimeData()`` and ``.acceptProposedAction()``, so a real Qt drag
    sequence (which needs a live window server even offscreen) isn't
    needed to exercise the drop-handling logic itself."""

    def __init__(self, mime_data) -> None:
        self._mime_data = mime_data
        self.accepted = False

    def mimeData(self):  # noqa: N802 - mirrors Qt's own method name
        return self._mime_data

    def acceptProposedAction(self) -> None:  # noqa: N802 - mirrors Qt's own method name
        self.accepted = True


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


def test_set_busy_shows_stop_and_keeps_the_box_usable(qapp):
    """Busy no longer means "locked out": Stop is its own button and the
    text box stays live, because text typed during a turn is now handed to
    that turn (user request: "so I can tell agent what I forgot")."""
    box = InputBox()
    assert not box.is_busy
    assert box._send_button.text() == "Send"
    assert not box._stop_button.isVisibleTo(box)

    box.set_busy(True)
    assert box.is_busy
    assert box._stop_button.isVisibleTo(box)
    assert box._send_button.text() == "Queue"
    assert box._text_edit.isEnabled()

    box.set_busy(False)
    assert not box.is_busy
    assert not box._stop_button.isVisibleTo(box)
    assert box._send_button.text() == "Send"


def test_stop_button_cancels_and_send_button_never_does(qapp):
    """The two used to be one button; conflating them is exactly what made
    typing-while-busy impossible."""
    box = InputBox()
    box.set_text("hello")
    box.set_busy(True)

    cancelled = []
    sent = []
    box.cancel_requested.connect(lambda: cancelled.append(True))
    box.send_requested.connect(sent.append)

    box._stop_button.click()
    assert cancelled == [True]
    assert sent == []

    box._send_button.click()
    assert cancelled == [True]  # unchanged: Send is only ever Send
    assert sent == ["hello"]


def test_enter_while_busy_sends_for_the_owner_to_queue(qapp):
    """InputBox doesn't know what "sending during a turn" means — it emits
    the same signal either way and MainWindow routes it to the running turn
    (ChatBridge.queue_user_message)."""
    box = InputBox()
    box.set_busy(True)
    sent = []
    box.send_requested.connect(sent.append)

    box._text_edit.setPlainText("also check the background")
    box._on_submit()

    assert sent == ["also check the background"]
    assert box.text() == ""  # cleared, like any other send


# --- Phase 6: attachments (Attach button + drag-and-drop) --------------------


def test_attach_button_opens_dialog_and_adds_paths(qapp, monkeypatch, tmp_path):
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")
    monkeypatch.setattr(
        "aida.ui.qt.input_box.QFileDialog.getOpenFileNames", lambda *a, **kw: ([str(file_a)], "")
    )

    box = InputBox()
    changed = []
    box.attachments_changed.connect(changed.append)
    box._on_attach_clicked()

    assert box.attached_paths() == [str(file_a)]
    assert changed == [[str(file_a)]]


def test_add_attachment_is_idempotent(qapp, tmp_path):
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")

    box = InputBox()
    box.add_attachment(str(file_a))
    box.add_attachment(str(file_a))

    assert box.attached_paths() == [str(file_a)]


def test_attachment_chip_remove_signal_removes_it(qapp, tmp_path):
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")

    box = InputBox()
    box.add_attachment(str(file_a))
    assert box.attached_paths() == [str(file_a)]

    # One chip widget sits before the trailing stretch item; clicking its
    # own remove button is what actually emits remove_requested in the UI —
    # exercised here by calling the handler directly, which is what that
    # click ultimately triggers.
    chip = box._attachments_row.itemAt(0).widget()
    assert chip.path == str(file_a)
    chip.remove_requested.emit(str(file_a))

    assert box.attached_paths() == []


def test_clear_attachments_empties_list_and_chips(qapp, tmp_path):
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")
    box = InputBox()
    box.add_attachment(str(file_a))

    box.clear_attachments()

    assert box.attached_paths() == []
    assert box._attachments_row.count() == 2  # just the stretch + busy label


def test_drop_files_adds_attachments(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file_a))])

    box = InputBox()
    event = _FakeDropEvent(mime)
    box.dropEvent(event)

    assert box.attached_paths() == [str(file_a)]
    assert event.accepted


def test_drop_folder_emits_folder_dropped_not_attached(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    folder = tmp_path / "some_dir"
    folder.mkdir()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(folder))])

    box = InputBox()
    dropped = []
    box.folder_dropped.connect(dropped.append)
    box.dropEvent(_FakeDropEvent(mime))

    assert dropped == [str(folder)]
    assert box.attached_paths() == []


def test_drop_mixed_files_and_folder(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")
    folder = tmp_path / "some_dir"
    folder.mkdir()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file_a)), QUrl.fromLocalFile(str(folder))])

    box = InputBox()
    dropped = []
    box.folder_dropped.connect(dropped.append)
    box.dropEvent(_FakeDropEvent(mime))

    assert box.attached_paths() == [str(file_a)]
    assert dropped == [str(folder)]


def test_send_does_not_automatically_clear_attachments(qapp, tmp_path):
    """InputBox itself never clears attachments on send — that's
    MainWindow's job (it reads attached_paths() right after send_requested
    fires, then explicitly calls clear_attachments()), so this pins down
    that InputBox doesn't do it prematurely on its own."""
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")
    box = InputBox()
    box.add_attachment(str(file_a))
    box.set_text("hello")

    box._send_button.click()

    assert box.attached_paths() == [str(file_a)]


def test_busy_state_is_visible_not_just_implied(qapp):
    """Bug report: while a turn ran, the only cues were a disabled text box
    and the button's label — easy to miss. Busy now also recolors the
    button and shows a live "Working…" line."""
    box = InputBox()

    assert box.busy_status_text() == ""
    assert not box._stop_button.isVisibleTo(box)

    box.set_busy(True)

    assert box._stop_button.isVisibleTo(box)
    assert box._stop_button.styleSheet()  # recolored, not a default button
    assert box._busy_label.isVisible() or box._busy_label.isVisibleTo(box)
    assert box.busy_status_text().startswith("Working")
    assert "Stop" in box.busy_status_text()

    box.set_busy(False)

    assert box._send_button.text() == "Send"
    assert not box._stop_button.isVisibleTo(box)
    assert box.busy_status_text() == ""
    assert not box._busy_label.isVisibleTo(box)


def test_busy_label_animates_and_counts_elapsed_time(qapp):
    box = InputBox()
    box.set_busy(True)
    first = box.busy_status_text()

    box._tick_busy_label()

    assert box.busy_status_text() != first  # the dots moved: not frozen


def test_repeated_set_busy_does_not_restart_the_clock(qapp):
    """turn_started can fire again for the same in-flight turn (a tool
    round trip); the elapsed counter must keep counting the turn."""
    box = InputBox()
    box.set_busy(True)
    box._busy_started_at -= 30  # pretend the turn began 30s ago
    box._tick_busy_label()

    box.set_busy(True)

    assert "30s" in box.busy_status_text()


def test_attachment_chips_survive_the_busy_indicator(qapp, tmp_path):
    """Regression guard for the attachments row now holding a trailing
    busy label as well as the stretch — refreshing chips must not eat
    either one."""
    file_a = tmp_path / "a.txt"
    file_a.write_text("hi", encoding="utf-8")
    box = InputBox()

    box.add_attachment(str(file_a))
    box.clear_attachments()
    box.add_attachment(str(file_a))

    assert box._attachments_row.count() == 3  # chip + stretch + busy label
    assert box._attachments_row.itemAt(0).widget().path == str(file_a)
    assert box._attachments_row.itemAt(2).widget() is box._busy_label
