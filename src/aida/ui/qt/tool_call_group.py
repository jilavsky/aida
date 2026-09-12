"""``ToolCallGroup``: one collapsible row standing for a *run* of
consecutive tool calls.

Bug report: "there are so many tool calls, that I do not see the other
prior parts of the chat... when I am trying to read the chat to see what
and how we did, these tool calls are distracting". ``ToolCallRow`` was
already collapsed and tightened as far as it usefully goes (see that
module's comments) — what was left was sheer *count*: one row per call,
thirty calls in a browser-automation or plotting turn, and the reply that
started them is off-screen. Grouping attacks the count instead of the
height: a whole run collapses to a single line, and the transcript reads
as user/assistant turns again.

The rows themselves are unchanged and always constructed, whatever the
display mode — from the same report: "it is important to be able to
inspect the tool calls when things go wrong... but my users at the
beamline may not be that interested". So a mode here only ever changes
*visibility*, never what exists: switching to "expanded" after a failure
shows that turn's calls in full, with no re-run and nothing lost. Do not
make ``hidden`` skip building rows — that trades the one property this
feature exists to preserve for a saving nobody asked for.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QFrame,
    QSizePolicy,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.tool_call_widget import ToolCallRow

#: The three values ``AppConfig.tool_call_display`` may take. Kept here
#: rather than in config so the widget that implements them owns them;
#: ``main_window`` imports this to build the View menu and to fall back
#: when config holds something unrecognized (a hand-edited config.yaml
#: must not be able to blank the transcript).
TOOL_CALL_DISPLAY_MODES = ("hidden", "grouped", "expanded")
DEFAULT_TOOL_CALL_DISPLAY = "grouped"


class ToolCallGroup(QFrame):
    """A header line plus a hidden column of ``ToolCallRow``s.

    Built empty and grown one row at a time by ``ChatPanel`` as
    ``ToolCallStarted`` events arrive; the header re-summarizes itself on
    every add and on every ``row_finished()``.
    """

    def __init__(
        self, parent: QWidget | None = None, *, mode: str = DEFAULT_TOOL_CALL_DISPLAY
    ) -> None:
        super().__init__(parent)
        self._rows: list[ToolCallRow] = []
        self._mode = mode if mode in TOOL_CALL_DISPLAY_MODES else DEFAULT_TOOL_CALL_DISPLAY
        self.setFrameShape(QFrame.Shape.NoFrame)

        outer = QVBoxLayout(self)
        # Same tight margins as ToolCallRow, for the same reason: this
        # widget exists to save vertical space, so it must not spend a
        # default 9-11px layout margin per side reclaiming it.
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        self._header = QToolButton(self)
        self._header.setCheckable(True)
        self._header.setChecked(False)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.RightArrow)
        # Expanding/Fixed with a word-wrapping-equivalent minimum: a
        # QToolButton's size hint is its full text width, and a header
        # naming a long tool would otherwise push the whole window wider,
        # exactly as the un-wrapped QLabel did in ToolCallRow (see that
        # module's first comment). The text here is bounded by
        # _MAX_HEADER_TOOL_NAME_CHARS instead, since a tool button cannot
        # word-wrap.
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self._on_header_clicked)
        outer.addWidget(self._header)

        self._body = QWidget(self)
        body_layout = QVBoxLayout(self._body)
        # Indented left, so an expanded group visibly nests under its
        # header rather than reading as more top-level transcript.
        body_layout.setContentsMargins(14, 0, 0, 0)
        body_layout.setSpacing(2)
        self._body.setVisible(False)
        outer.addWidget(self._body)

        self._apply_mode()

    # --- building ----------------------------------------------------------

    def add_row(self, row: ToolCallRow) -> None:
        row.setParent(self._body)
        self._body.layout().addWidget(row)
        self._rows.append(row)
        self._refresh_header()

    def row_finished(self) -> None:
        """Call after any contained row's ``mark_finished`` — the header's
        tally and elapsed total are derived from the rows, so they are
        stale until something asks for a recount."""
        self._refresh_header()

    @property
    def rows(self) -> list[ToolCallRow]:
        return list(self._rows)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    # --- display mode -------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in TOOL_CALL_DISPLAY_MODES:
            mode = DEFAULT_TOOL_CALL_DISPLAY
        self._mode = mode
        self._apply_mode()
        self._refresh_header()

    @property
    def mode(self) -> str:
        return self._mode

    def _apply_mode(self) -> None:
        """A mode change wins over a hand-toggled group.

        Deliberate: the modes are how the user says "I am reading" versus
        "I am debugging", and a switch that left half the transcript in
        the old state would not answer either question. Expanding one
        group by hand is a per-group action; changing the mode is a
        statement about the whole transcript.
        """
        expanded = self._mode == "expanded"
        self._set_expanded(expanded)
        # In "hidden" the header keeps its arrow and stays clickable — the
        # stub is the only signal that anything is there to open, and
        # retroactive inspection is the requirement this feature is built
        # around. It is only styled down, not disabled.
        if self._mode == "hidden":
            self._header.setStyleSheet(
                "QToolButton { border: none; background: transparent; "
                "color: palette(mid); font-size: 10px; padding: 0px 2px; }"
            )
        else:
            self._header.setStyleSheet(
                "QToolButton { border: none; background: transparent; "
                "color: gray; font-size: 11px; padding: 1px 2px; }"
                "QToolButton:hover { color: palette(text); }"
            )

    def _on_header_clicked(self) -> None:
        self._set_expanded(self._header.isChecked())

    def _set_expanded(self, expanded: bool) -> None:
        self._header.setChecked(expanded)
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._body.setVisible(expanded)

    @property
    def is_expanded(self) -> bool:
        return self._header.isChecked()

    # --- header text --------------------------------------------------------

    def _refresh_header(self) -> None:
        self._header.setText(self.header_text())

    def header_text(self) -> str:
        """Exposed (rather than only set on the button) so tests can assert
        on the summary without reaching into Qt's realized state."""
        total = len(self._rows)
        noun = "tool call" if total == 1 else "tool calls"
        if self._mode == "hidden":
            # Deliberately bare: no tally, no timing, no running tool. The
            # beamline preset — enough to know something happened and that
            # it can be opened, nothing that invites reading.
            return f"{total} {noun}"

        pending = [row for row in self._rows if row.is_error is None and not row.is_historic]
        if pending:
            # Live feedback in one line, replacing what the individual
            # in-flight rows used to provide. Without naming the current
            # tool a slow call is indistinguishable from a hang, which is
            # the failure mode the "⏳ ... …" per-row marker existed to
            # prevent — so the name stays, just bounded.
            name = _abbreviate(pending[-1].tool_name)
            return f"⏳ {total} {noun} · running {name}…"

        ok = sum(1 for row in self._rows if row.is_error is False)
        failed = sum(1 for row in self._rows if row.is_error is True)
        parts = [f"{total} {noun}"]
        tally = []
        if ok:
            tally.append(f"{ok} ✓")
        if failed:
            tally.append(f"{failed} ✗")
        if tally:
            parts.append(" ".join(tally))
        elapsed = [row.elapsed for row in self._rows if row.elapsed is not None]
        if elapsed:
            parts.append(f"{sum(elapsed):.1f} s")
        return " · ".join(parts)


#: A tool name long enough to widen the window is truncated rather than
#: wrapped: QToolButton has no word-wrap, and an un-wrapped label's size
#: hint propagates up as a hard width floor (the bug ToolCallRow's first
#: comment documents at length).
_MAX_HEADER_TOOL_NAME_CHARS = 40


def _abbreviate(name: str) -> str:
    if len(name) <= _MAX_HEADER_TOOL_NAME_CHARS:
        return name
    return name[: _MAX_HEADER_TOOL_NAME_CHARS - 1] + "…"


__all__ = ["ToolCallGroup", "TOOL_CALL_DISPLAY_MODES", "DEFAULT_TOOL_CALL_DISPLAY"]
