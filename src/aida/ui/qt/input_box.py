"""``InputBox`` (PLAN.md Phase 5): "Input box: multiline, Enter/Shift-Enter,
Send + Stop button (cancel works mid-stream), busy indicator".

Two signals are the whole public contract: ``send_requested(str)`` (Enter,
or the button while idle) and ``cancel_requested()`` (the button while
busy, which is exactly ``ChatBridge.cancel()``'s job) — this widget has no
idea a bridge or asyncio exists, same as every other widget here.

Phase 6 adds file attachments: drag-and-drop onto the box, or the "Attach…"
button, both just collect local file *paths* into ``attached_paths()`` —
a send with attachments but no typed text is allowed (an empty box no
longer blocks it once something is attached — see ``_on_submit``), so
"attach a file and just hit send" works —
this widget never reads file content itself (that stays plain data/paths,
consistent with every other widget here not knowing about
``aida.documents``/``aida.workspace`` — ``MainWindow`` reads the attached
paths via ``aida.documents.readers`` right before forwarding the turn, and
clears them after). A dropped *folder* is a different signal
(``folder_dropped``) since "add this as an allowed folder" is a decision
``MainWindow`` needs to mediate (confirm dialog + workspace config), not
something this widget can decide on its own.
"""

from __future__ import annotations

from pathlib import Path

from aida.ui.qt._qt import (
    QFileDialog,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)


class _AttachmentChip(QWidget):
    """One small "filename ×" pill in the attachments row."""

    remove_requested = Signal(str)

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addWidget(QLabel(Path(path).name, self))
        remove_button = QPushButton("×", self)
        remove_button.setFixedWidth(20)
        remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))
        layout.addWidget(remove_button)


class _InputTextEdit(QPlainTextEdit):
    """Enter submits; Shift+Enter (or Ctrl+Enter, for muscle memory from
    other chat apps) inserts a literal newline instead."""

    submit_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if is_enter and not (event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class InputBox(QWidget):
    send_requested = Signal(str)
    cancel_requested = Signal()
    folder_dropped = Signal(str)
    attachments_changed = Signal(list)  # current list of attached paths

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._attachments: list[str] = []
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._attachments_row = QHBoxLayout()
        self._attachments_row.addStretch(1)
        outer.addLayout(self._attachments_row)

        layout = QHBoxLayout()
        outer.addLayout(layout)
        self._text_edit = _InputTextEdit(self)
        self._text_edit.setPlaceholderText("Message AIDA… (Enter to send, Shift+Enter for a new line)")
        self._text_edit.submit_requested.connect(self._on_submit)
        layout.addWidget(self._text_edit)

        button_column = QVBoxLayout()
        self._send_button = QPushButton("Send", self)
        self._send_button.setShortcut(QKeySequence("Ctrl+Return"))
        self._send_button.clicked.connect(self._on_button_clicked)
        button_column.addWidget(self._send_button)
        self._attach_button = QPushButton("Attach…", self)
        self._attach_button.clicked.connect(self._on_attach_clicked)
        button_column.addWidget(self._attach_button)
        layout.addLayout(button_column)

    # --- state -----------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Called by whatever owns this widget (MainWindow) on
        ``ChatBridge.turn_started``/``turn_finished`` — while busy, the
        text box is disabled (a new turn can't start until this one ends
        or is cancelled) and the button becomes Stop."""
        self._busy = busy
        self._text_edit.setEnabled(not busy)
        self._send_button.setText("Stop" if busy else "Send")

    # --- text ----------------------------------------------------------------

    def text(self) -> str:
        return self._text_edit.toPlainText()

    def clear(self) -> None:
        self._text_edit.clear()

    def set_text(self, text: str) -> None:
        self._text_edit.setPlainText(text)

    # --- attachments -----------------------------------------------------

    def attached_paths(self) -> list[str]:
        return list(self._attachments)

    def clear_attachments(self) -> None:
        self._attachments = []
        self._refresh_attachment_chips()

    def add_attachment(self, path: str) -> None:
        if path in self._attachments:
            return
        self._attachments.append(path)
        self._refresh_attachment_chips()

    def _remove_attachment(self, path: str) -> None:
        if path in self._attachments:
            self._attachments.remove(path)
            self._refresh_attachment_chips()

    def _refresh_attachment_chips(self) -> None:
        while self._attachments_row.count() > 1:  # leave the trailing stretch alone
            item = self._attachments_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for path in self._attachments:
            chip = _AttachmentChip(path, self)
            chip.remove_requested.connect(self._remove_attachment)
            self._attachments_row.insertWidget(self._attachments_row.count() - 1, chip)
        self.attachments_changed.emit(list(self._attachments))

    def _on_attach_clicked(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Attach Files")
        for path in paths:
            self.add_attachment(path)

    # --- drag and drop -----------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        urls = event.mimeData().urls()
        if not urls:
            super().dropEvent(event)
            return
        for url in urls:
            local_path = url.toLocalFile()
            if not local_path:
                continue
            if Path(local_path).is_dir():
                self.folder_dropped.emit(local_path)
            else:
                self.add_attachment(local_path)
        event.acceptProposedAction()

    # --- actions -------------------------------------------------------------

    def _on_submit(self) -> None:
        if self._busy:
            return  # Enter while a turn is in flight does nothing — use Stop
        text = self.text().strip()
        if not text and not self._attachments:
            return  # nothing to send: no typed text and nothing attached
        self.clear()
        self.send_requested.emit(text)

    def _on_button_clicked(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        else:
            self._on_submit()


__all__ = ["InputBox"]
