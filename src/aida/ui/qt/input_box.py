"""``InputBox`` (PLAN.md Phase 5): "Input box: multiline, Enter/Shift-Enter,
Send + Stop button (cancel works mid-stream), busy indicator".

Two signals are the whole public contract: ``send_requested(str)`` (Enter,
or the button while idle) and ``cancel_requested()`` (the button while
busy, which is exactly ``ChatBridge.cancel()``'s job) — this widget has no
idea a bridge or asyncio exists, same as every other widget here.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QHBoxLayout,
    QKeySequence,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False

        layout = QHBoxLayout(self)
        self._text_edit = _InputTextEdit(self)
        self._text_edit.setPlaceholderText("Message AIDA… (Enter to send, Shift+Enter for a new line)")
        self._text_edit.submit_requested.connect(self._on_submit)
        layout.addWidget(self._text_edit)

        button_column = QVBoxLayout()
        self._send_button = QPushButton("Send", self)
        self._send_button.setShortcut(QKeySequence("Ctrl+Return"))
        self._send_button.clicked.connect(self._on_button_clicked)
        button_column.addWidget(self._send_button)
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

    # --- actions -------------------------------------------------------------

    def _on_submit(self) -> None:
        if self._busy:
            return  # Enter while a turn is in flight does nothing — use Stop
        text = self.text().strip()
        if not text:
            return
        self.clear()
        self.send_requested.emit(text)

    def _on_button_clicked(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        else:
            self._on_submit()


__all__ = ["InputBox"]
