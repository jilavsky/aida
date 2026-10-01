"""``ConversationHeader`` — the name of the conversation you are currently
in, at the top of the chat column.

Bug report: "unless user renames the chat, the chat name is start of the
user question — rarely useful", and there was nowhere in the window that
showed the name at all, so there was no feedback that a conversation even
had one and no obvious place to fix it. Conversations now name themselves
from their content (``aida.core.titling``); this is where the user sees
that happen, and the shortest path to overriding it.

Deliberately dumb, the same way ``aida.ui.qt.conversations_sidebar`` is: it
is told what to display (``set_conversation``) and only emits a *request*
(``rename_requested``) — ``aida.ui.qt.main_window`` owns the dialog and the
actual write to ``aida.persistence``.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QFontMetrics,
    QFrame,
    QHBoxLayout,
    QLabel,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
    Signal,
)

#: Shown in place of a title for a conversation nobody has said anything in
#: yet — the recorder only derives a placeholder title on the first user
#: message, and a blank strip there reads as a rendering bug.
PLACEHOLDER_TITLE = "New conversation"


class _ElidingLabel(QLabel):
    """A ``QLabel`` that shortens its text to the width it is actually
    given, instead of demanding the width its text would like.

    The chat column sits in a user-draggable ``QSplitter`` whose other two
    panes are expected to be dragged down to very little (see
    ``conversations_sidebar.MIN_SIDEBAR_WIDTH`` for the same lesson learned
    the hard way): a header holding one long title must never become the
    reason the chat column cannot shrink. ``setMinimumWidth(0)`` plus
    re-eliding on every resize is what guarantees that; the untruncated
    text stays reachable as the tooltip.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def _apply_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        available = max(0, self.width())
        if available <= 0:
            # Before the first layout pass there is no width to elide to;
            # show the real text and let the resize below correct it.
            super().setText(self._full_text)
            return
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_elide()


class ConversationHeader(QFrame):
    """One compact strip: the conversation's name, and underneath it which
    workspace/provider it is running in."""

    #: The user wants to name this conversation themselves. Carries no id —
    #: this widget has no idea which conversation it is showing, and
    #: ``MainWindow`` already knows (``_active_conversation_id``).
    rename_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(0)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(0)

        self._title_label = _ElidingLabel(self)
        title_font = self._title_label.font()
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        text_column.addWidget(self._title_label)

        self._subtitle_label = _ElidingLabel(self)
        subtitle_font = self._subtitle_label.font()
        # Relative, not absolute: the app has a user-configurable font size
        # (AppConfig.font_size) and this line has to stay proportional to it.
        subtitle_font.setPointSizeF(max(6.0, subtitle_font.pointSizeF() * 0.85))
        self._subtitle_label.setFont(subtitle_font)
        self._subtitle_label.setEnabled(False)  # the palette's own "dim", theme-correct
        text_column.addWidget(self._subtitle_label)

        outer.addLayout(text_column, stretch=1)

        self._rename_button = QToolButton(self)
        self._rename_button.setText("✎")
        self._rename_button.setAutoRaise(True)
        self._rename_button.setToolTip("Rename this conversation")
        self._rename_button.clicked.connect(self.rename_requested.emit)
        outer.addWidget(self._rename_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.set_conversation(None, "")

    def set_conversation(self, title: str | None, subtitle: str = "") -> None:
        """Show ``title`` (or the placeholder, for a conversation with no
        name yet) over ``subtitle``. An empty subtitle hides that line
        entirely rather than leaving a blank gap."""
        self._title_label.set_full_text((title or "").strip() or PLACEHOLDER_TITLE)
        self._subtitle_label.set_full_text(subtitle)
        self._subtitle_label.setVisible(bool(subtitle))

    def title_text(self) -> str:
        """The full (un-elided) title currently shown — what the rename
        dialog pre-fills with, and what tests assert on."""
        return self._title_label.full_text()

    def subtitle_text(self) -> str:
        return self._subtitle_label.full_text()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Double-clicking the strip renames it — the gesture people try
        on a title before they look for a button, and the same one the
        sidebar's list already answers (there, by resuming)."""
        self.rename_requested.emit()
        event.accept()


__all__ = ["PLACEHOLDER_TITLE", "ConversationHeader"]
