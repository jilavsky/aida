"""``ToolCallRow`` (PLAN.md Phase 5): "Tool-call indicators: collapsed
'server.tool(args…) ✓/✗ (1.2 s)' rows, expandable to arguments/results
(text form)".

One row is created on ``ToolCallStarted`` and updated in place on the
matching ``ToolCallFinished`` (matched by ``call_id``) — mirrors how
``aida.cli.chat.print_event`` treats the pair as one logical unit, just
rendered as a single updatable widget instead of two printed lines.
"""

from __future__ import annotations

import time

from aida.ui.qt._qt import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

#: Bug report: a tool call with a long argument list (e.g. plotting many
#: datasets at once — a `paths=[...]` list with dozens of file paths)
#: rendered as one unbroken line, and the collapsed row's QLabel had no
#: word wrap, so its minimum size hint was the full unwrapped text width —
#: with that label inside a plain QHBoxLayout (no width constraint of its
#: own), the row demanded that full width from its parents all the way up
#: to the QMainWindow, growing the whole app window "to semi-infinite
#: size" rather than staying put and letting the text wrap. Word-wrapping
#: the label (below) fixes the propagation; capping the *summary* line
#: length here on top of that keeps the collapsed view scannable even for
#: a genuinely huge argument dict — the full, untruncated arguments are
#: always available via "Details" (mark_finished stores them verbatim).
#:
#: Bug report: "a lot of tool calls... all I see are tool calls which are
#: taking pages of space (Playwright). I need to be able to inspect them,
#: but they could be smaller by default (vertically)". Rows were already
#: collapsed by default (Details starts hidden, below), but at 300 chars a
#: word-wrapped summary for a call with several/long arguments (routine
#: for browser-automation tools like Playwright's) still ran several lines
#: tall, and with "a lot of tool calls" that adds up to real scrolling.
#: Shrunk so the common case is one, at most two, lines — still enough to
#: recognize the call at a glance, with the untruncated arguments one
#: "Details" click away either way.
_MAX_SUMMARY_ARGS_CHARS = 120

#: Same bug report: bound the *expanded* detail view too, so clicking
#: "Details" on one huge Playwright snapshot/result can't itself push
#: everything below it off-screen — QTextEdit already scrolls internally,
#: this just keeps that scrolling from starting only after swallowing most
#: of the visible transcript.
_MAX_DETAIL_HEIGHT_PX = 240


def _format_arguments(arguments: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    if len(text) > _MAX_SUMMARY_ARGS_CHARS:
        return text[: _MAX_SUMMARY_ARGS_CHARS - 1] + "…"
    return text


class ToolCallRow(QFrame):
    """Starts collapsed, showing ``server.tool(args) …`` while the call is
    in flight, then ``✓``/``✗`` plus elapsed seconds once
    ``mark_finished()`` is called. The expand button reveals full
    arguments + result text."""

    def __init__(self, *, call_id: str, tool_name: str, arguments: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.result: object | None = None
        self.is_error: bool | None = None
        self._started_at = time.monotonic()
        self._elapsed: float | None = None
        self._expanded = False
        # U6: set by mark_historic() for a row rebuilt from a resumed
        # conversation's persisted ``Message`` — see that method's docstring
        # for why it can't know success/failure or elapsed time.
        self._historic = False
        self.setFrameShape(QFrame.Shape.StyledPanel)

        outer = QVBoxLayout(self)
        # Bug report: rows were taking "pages of space" with a lot of tool
        # calls in the transcript (Playwright-heavy sessions especially) —
        # Qt's default QVBoxLayout/QHBoxLayout margins/spacing (9-11px a
        # side) add up fast across dozens of collapsed rows. Tightened
        # alongside the shorter summary cap above.
        outer.setContentsMargins(6, 3, 6, 3)
        outer.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(6)
        self._summary_label = QLabel(self)
        # Word-wrap is the actual fix for the reported bug: a QLabel
        # without it has a minimum size hint equal to its full, unwrapped
        # text width, which a plain QHBoxLayout propagates straight up to
        # the enclosing QMainWindow as a hard width floor — this is what
        # forced the whole app window wider for a long tool-call argument
        # list. With word wrap on, the label's minimum shrinks to its
        # longest unbreakable token, so it wraps to fit whatever width the
        # window actually has instead of demanding more.
        self._summary_label.setWordWrap(True)
        header.addWidget(self._summary_label, stretch=1)
        self._toggle_button = QPushButton("Details", self)
        self._toggle_button.clicked.connect(self.toggle_expanded)
        header.addWidget(self._toggle_button)
        outer.addLayout(header)

        self._detail_text = QTextEdit(self)
        self._detail_text.setReadOnly(True)
        self._detail_text.setMaximumHeight(_MAX_DETAIL_HEIGHT_PX)
        self._detail_text.hide()
        outer.addWidget(self._detail_text)

        self._refresh_summary()

    def _refresh_summary(self) -> None:
        args = _format_arguments(self.arguments)
        base = f"{self.tool_name}({args})"
        if self.is_error is None:
            if self._historic:
                # U6: a resumed tool row — success/failure was never
                # persisted on the ``Message`` this was rebuilt from (see
                # mark_historic), so a neutral marker is shown rather than
                # guessing ✓/✗.
                self._summary_label.setText(f"• {base}")
            else:
                self._summary_label.setText(f"⏳ {base} …")
        else:
            mark = "✗" if self.is_error else "✓"
            elapsed = f"{self._elapsed:.1f}s" if self._elapsed is not None else "?"
            self._summary_label.setText(f"{mark} {base} ({elapsed})")

    def mark_finished(self, *, result: object, is_error: bool) -> None:
        self.result = result
        self.is_error = is_error
        self._elapsed = time.monotonic() - self._started_at
        self._refresh_summary()
        self._detail_text.setPlainText(f"Arguments:\n{self.arguments!r}\n\nResult:\n{result}")

    def mark_historic(self, *, result: object) -> None:
        """U6: render as a collapsed, already-finished row for a tool
        message rebuilt from persisted history (``ChatPanel.load_history``)
        rather than a live ``ToolCallFinished`` event — ``Message`` doesn't
        persist ``is_error`` or elapsed time (see ``aida.core.agent``), so
        this shows a neutral "•" marker and no duration instead of guessing
        ✓/✗. Collapsing every resumed tool message into one of these rows
        (instead of a full-text bubble, the previous behavior) is the fix
        for the bug report that a resumed analysis session "replays as a
        wall of raw tool output"."""
        self.result = result
        self.is_error = None
        self._elapsed = None
        self._historic = True
        self._refresh_summary()
        self._detail_text.setPlainText(f"Arguments:\n{self.arguments!r}\n\nResult:\n{result}")

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._detail_text.setVisible(self._expanded)

    @property
    def is_expanded(self) -> bool:
        return self._expanded


__all__ = ["ToolCallRow"]
