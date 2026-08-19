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


def _format_arguments(arguments: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in arguments.items())


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
        self.setFrameShape(QFrame.Shape.StyledPanel)

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self._summary_label = QLabel(self)
        header.addWidget(self._summary_label)
        self._toggle_button = QPushButton("Details", self)
        self._toggle_button.clicked.connect(self.toggle_expanded)
        header.addWidget(self._toggle_button)
        outer.addLayout(header)

        self._detail_text = QTextEdit(self)
        self._detail_text.setReadOnly(True)
        self._detail_text.hide()
        outer.addWidget(self._detail_text)

        self._refresh_summary()

    def _refresh_summary(self) -> None:
        args = _format_arguments(self.arguments)
        base = f"{self.tool_name}({args})"
        if self.is_error is None:
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

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._detail_text.setVisible(self._expanded)

    @property
    def is_expanded(self) -> bool:
        return self._expanded


__all__ = ["ToolCallRow"]
