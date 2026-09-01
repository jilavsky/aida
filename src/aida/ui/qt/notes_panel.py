"""``NotesPanel``: a per-workspace scratch pad.

User request: "users really need a workspace notepad — simple text file in
which to make notes about the workspace... space for notes to make while
they are working, so they can note what to do next or what they observed
and needs fixing etc. It needs to be saved with workspace."

Two decisions worth stating, because both are load-bearing:

**The notes are private.** Nothing typed here is added to the system
prompt, and no tool can read it. A running commentary the user keeps for
themselves must not quietly become tokens on every request, and half-formed
notes ("check whether the fit is wrong?") must not steer the model. If the
user wants the agent to see a note, they paste it into a message — an
explicit act, same as any other input.

**Saving is debounced, not on every keystroke.** ``notes_changed`` fires
``_SAVE_DEBOUNCE_MS`` after typing stops, so a paragraph is one
``workspaces.yaml`` write rather than three hundred. ``flush()`` exists for
the two moments that can't wait for the timer: closing the window, and
switching away from the workspace whose notes are on screen.

Deliberately dumb about persistence, like every other widget in
``aida.ui.qt``: it holds text and emits a signal — ``aida.ui.qt.
main_window`` is the one place that reads/writes ``WorkspaceConfig.notes``.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QTimer,
    QVBoxLayout,
    QWidget,
    Signal,
)

#: Quiet period after the last keystroke before the notes are persisted.
_SAVE_DEBOUNCE_MS = 1500

_PLACEHOLDER = "Notes for this workspace — what to do next, what looked wrong, anything to come back to."


class NotesPanel(QGroupBox):
    """Free-text notes for the active workspace."""

    #: Full replacement text — persist this.
    notes_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Workspace Notes", parent)
        layout = QVBoxLayout(self)

        self._edit = QPlainTextEdit(self)
        self._edit.setPlaceholderText(_PLACEHOLDER)
        self._edit.setMinimumHeight(120)
        self._edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._edit)

        self._status = QLabel("", self)
        self._status.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._status)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.flush)
        #: Set while set_notes() is repopulating the box, so loading a
        #: workspace's saved notes doesn't look like the user typing them
        #: and immediately save them straight back.
        self._loading = False
        #: Whether there is an edit still to emit — see flush().
        self._pending_edit = False

    # --- text ------------------------------------------------------------

    def notes(self) -> str:
        return self._edit.toPlainText()

    def set_notes(self, text: str) -> None:
        """Populate from the active workspace. Any pending save for the
        *previous* workspace is flushed first — otherwise switching
        workspaces within the debounce window would write the outgoing
        workspace's last sentence into the incoming one."""
        self.flush()
        self._loading = True
        try:
            self._edit.setPlainText(text)
        finally:
            self._loading = False
        self._status.setText("")

    # --- saving ----------------------------------------------------------

    def flush(self) -> None:
        """Emit any pending edit now. Safe to call when nothing is pending —
        it's a no-op, so callers (window close, workspace switch) don't need
        to know whether the timer is armed.

        "Pending" is its own flag rather than ``_save_timer.isActive()``.
        That distinction is the whole bug this method shipped with: a
        single-shot QTimer is *not* active any more by the time its own
        ``timeout`` handler runs, so the debounced save — the normal path,
        the one that fires when you simply stop typing — returned here
        immediately and emitted nothing. Notes stayed on "Saving…" forever
        and were lost on the next workspace switch. Every test called
        ``flush()`` by hand while the timer was still armed, which is
        exactly the one situation where reading ``isActive()`` happened to
        work.
        """
        if not self._pending_edit:
            return
        self._pending_edit = False
        self._save_timer.stop()
        self._status.setText("Saved")
        self.notes_changed.emit(self.notes())

    @property
    def has_unsaved_edit(self) -> bool:
        return self._pending_edit

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._pending_edit = True
        self._status.setText("Saving…")
        self._save_timer.start()  # restarts the quiet period on every keystroke


__all__ = ["NotesPanel"]
