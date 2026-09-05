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

import os
import time
from pathlib import Path

from aida.ui.qt._qt import (
    QFileDialog,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    Signal,
)

_IDLE_PLACEHOLDER = "Message AIDA… (Enter to send, Shift+Enter for a new line)"
#: While a turn runs, Enter queues rather than starts — say so, since the
#: same key now does two different things depending on state.
_BUSY_PLACEHOLDER = "Agent is working — type to add a note for its next step (Enter to queue)"

#: Bug report: "when model is working I can only not write new message and
#: the Stop button is visible" — the *absence* of the Send button was the
#: only signal that a turn was in flight, which is a thing you have to
#: notice rather than a thing you see. Stop is now its own button, red
#: (the one colored button in the window: red = "this stops something"),
#: visible only while a turn is running, with a live "Working… 12s" label
#: beside it — so both the state and how long it has been going are
#: readable at a glance.
_STOP_BUTTON_STYLE = """
QPushButton {
    background-color: #c0392b;
    color: white;
    font-weight: bold;
    border: 1px solid #96281b;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover { background-color: #d64535; }
QPushButton:pressed { background-color: #96281b; }
"""

#: How often the "Working…" label repaints. Sub-second so the animated
#: dots read as motion (the point: motion is what says "not frozen"),
#: while the elapsed seconds it also shows only change every other tick.
_BUSY_TICK_MS = 500


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
        if is_enter and not (
            event.modifiers()
            & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        ):
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class InputBox(QWidget):
    send_requested = Signal(str)
    cancel_requested = Signal()
    folder_dropped = Signal(str)
    attachments_changed = Signal(list)  # current list of attached paths
    #: Phase 10: every keystroke in the prompt box. The in-app scheduler
    #: treats typing as user activity (so its quiet period restarts) and
    #: refuses to start a job while there is unsent text — a scheduled run
    #: must never land in the middle of someone composing a prompt. Only
    #: re-exported here because reaching into the internal text widget from
    #: MainWindow would couple it to this widget's private layout.
    text_changed = Signal()

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
        self._text_edit.setPlaceholderText(_IDLE_PLACEHOLDER)
        self._text_edit.submit_requested.connect(self._on_submit)
        self._text_edit.textChanged.connect(self.text_changed.emit)
        layout.addWidget(self._text_edit)

        button_column = QVBoxLayout()
        self._send_button = QPushButton("Send", self)
        self._send_button.setShortcut(QKeySequence("Ctrl+Return"))
        self._send_button.clicked.connect(self._on_submit)
        button_column.addWidget(self._send_button)
        # Its own button, not a relabelled Send: now that typing during a
        # turn is allowed (see _on_submit), one button cannot mean both
        # "send this text" and "stop the turn" — the user needs Send to
        # stay Send while the agent works.
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.setStyleSheet(_STOP_BUTTON_STYLE)
        self._stop_button.setToolTip("Stop the turn in progress")
        self._stop_button.clicked.connect(self.cancel_requested.emit)
        self._stop_button.setVisible(False)
        button_column.addWidget(self._stop_button)
        self._attach_button = QPushButton("Attach…", self)
        self._attach_button.clicked.connect(self._on_attach_clicked)
        button_column.addWidget(self._attach_button)
        layout.addLayout(button_column)

        # Sits at the right end of the attachments row (above the text box,
        # next to the button column) so it appears where the user is
        # already looking when they wonder whether anything is happening.
        self._busy_label = QLabel("", self)
        self._busy_label.setStyleSheet("color: #c0392b;")
        self._busy_label.setVisible(False)
        self._attachments_row.addWidget(self._busy_label)

        self._busy_started_at = 0.0
        self._busy_ticks = 0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(_BUSY_TICK_MS)
        self._busy_timer.timeout.connect(self._tick_busy_label)

    # --- state -----------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Called by whatever owns this widget (MainWindow) on
        ``ChatBridge.turn_started``/``turn_finished`` — while busy, the
        red **Stop** button appears, Send becomes **Queue**, and the
        "Working… Ns" label ticks beside them (see ``_STOP_BUTTON_STYLE``).

        The text box deliberately stays *enabled* while busy: text typed
        during a turn is handed to that turn rather than rejected (see
        ``_on_submit``), which is the whole point of Stop being its own
        button now.

        Idempotent: a repeated ``set_busy(True)`` does not restart the
        elapsed clock, so a turn's timer keeps counting the turn rather
        than the last signal.
        """
        if busy == self._busy:
            return
        self._busy = busy
        self._stop_button.setVisible(busy)
        self._send_button.setText("Queue" if busy else "Send")
        self._send_button.setToolTip(
            "Hand this to the running turn — the agent sees it at its next step" if busy else ""
        )
        self._text_edit.setPlaceholderText(_BUSY_PLACEHOLDER if busy else _IDLE_PLACEHOLDER)
        if busy:
            self._busy_started_at = time.monotonic()
            self._busy_ticks = 0
            self._busy_label.setVisible(True)
            self._tick_busy_label()
            self._busy_timer.start()
        else:
            self._busy_timer.stop()
            self._busy_label.setVisible(False)
            self._busy_label.setText("")

    def busy_status_text(self) -> str:
        """Current "Working…" text (empty when idle) — the readable form of
        this widget's busy state, and what the tests assert on."""
        return self._busy_label.text()

    def _tick_busy_label(self) -> None:
        elapsed = int(time.monotonic() - self._busy_started_at)
        dots = "." * (1 + self._busy_ticks % 3)
        self._busy_ticks += 1
        self._busy_label.setText(f"Working{dots} {elapsed}s — press Stop to cancel")

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
        # Windows CI regression: Qt's own APIs (QFileDialog's return values,
        # QUrl.toLocalFile()) hand back "Qt-style" paths using forward
        # slashes even on Windows — a long-documented Qt quirk, not a bug in
        # this code, but every caller comparing against a native
        # (backslash) path failed. normpath() converts to the OS's native
        # separator (a no-op on POSIX) without touching drive letters or
        # relative segments in a way that changes what the path points at.
        path = os.path.normpath(path)
        if path in self._attachments:
            return
        self._attachments.append(path)
        self._refresh_attachment_chips()

    def _remove_attachment(self, path: str) -> None:
        if path in self._attachments:
            self._attachments.remove(path)
            self._refresh_attachment_chips()

    def _refresh_attachment_chips(self) -> None:
        # Removes chips *by type* rather than "everything but the last
        # item": this row is [chips…, stretch, busy label], so the two
        # non-chip items at the end must both survive a refresh — an
        # index-based sweep silently ate the stretch once the busy label
        # was added, which left the chips pinned to the right edge.
        for index in reversed(range(self._attachments_row.count())):
            widget = self._attachments_row.itemAt(index).widget()
            if isinstance(widget, _AttachmentChip):
                self._attachments_row.takeAt(index)
                widget.deleteLater()
        for index, path in enumerate(self._attachments):
            chip = _AttachmentChip(path, self)
            chip.remove_requested.connect(self._remove_attachment)
            self._attachments_row.insertWidget(index, chip)
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
            local_path = os.path.normpath(local_path)  # see add_attachment's docstring
            if Path(local_path).is_dir():
                self.folder_dropped.emit(local_path)
            else:
                self.add_attachment(local_path)
        event.acceptProposedAction()

    # --- actions -------------------------------------------------------------

    def _on_submit(self) -> None:
        """Enter, or the Send/Queue button.

        Emits ``send_requested`` whether or not a turn is running — this
        widget does not decide what "sending during a turn" means, the
        owner does (``MainWindow`` hands it to the running turn via
        ``ChatBridge.queue_user_message``). Enter used to be a no-op while
        busy; it now works, which is the user-visible half of "let me tell
        the agent what I forgot".
        """
        text = self.text().strip()
        if not text and not self._attachments:
            return  # nothing to send: no typed text and nothing attached
        self.clear()
        self.send_requested.emit(text)


__all__ = ["InputBox"]
