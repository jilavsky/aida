"""``ChatPanel`` (PLAN.md Phase 5): "Conversation view: user/assistant
turns, streamed text appended live, Markdown rendering (Qt rich text)... /
Tool-call indicators... / Error display distinguishes layer".

This is the widget ``aida.ui.qt.bridge.ChatBridge.event_received`` is meant
to be connected to — one ``handle_event(event)`` call per
``aida.core.events.AgentEvent``, dispatching exactly like
``aida.cli.chat.print_event`` does for the CLI, just building/updating
widgets instead of printing lines. Nothing here knows about ``ChatBridge``
or asyncio at all — it only consumes plain ``AgentEvent`` values, so it can
be driven directly in tests without a real bridge/session.
"""

from __future__ import annotations

from collections.abc import Iterable

from aida.providers.base import Message
from aida.ui.qt._qt import (
    QFrame,
    QGuiApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    Qt,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from aida.ui.qt.tool_call_widget import ToolCallRow


class _AutoHeightTextBrowser(QTextBrowser):
    """A ``QTextBrowser`` that grows to fit its content instead of
    scrolling internally — bug report: "the box has a fixed height and if
    the reply is larger, it is impossible to read without scrolling; make
    the box automatically adjust the height." ``ChatPanel``'s own
    ``QScrollArea`` already scrolls the whole conversation; a second,
    internally-scrolling viewport per message just hides most of a long
    reply behind a tiny fixed-size window and adds a confusing second
    scrollbar. Also borderless/transparent by default (see ``MessageBubble``
    below for why) — a plain ``QTextBrowser`` paints its own sunken-panel
    frame and opaque background, which is the actual source of each
    message looking like its own separate "box"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.document().documentLayout().documentSizeChanged.connect(self._recalculate_height)
        self._recalculate_height()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Word-wrapped text's height depends on the available width, so a
        # width change (e.g. the user resizing the window/splitter) needs
        # the same recalculation a content change does.
        super().resizeEvent(event)
        self._recalculate_height()

    def _recalculate_height(self, *_args: object) -> None:
        height = self.document().size().height()
        margins = self.contentsMargins()
        self.setFixedHeight(int(height) + margins.top() + margins.bottom() + 4)


class MessageBubble(QFrame):
    """One user/assistant turn. Assistant turns are built incrementally
    (``append_delta`` per ``TextDelta``, ``set_text`` with the final full
    text on ``TextFinished``); a user turn or a resumed history message is
    just ``set_text`` once.

    Styling (bug report: "the box with agent reply... did not have borders
    and was invisible to user. Just have user question/prompt to be
    visibly different style, agent replies should look like continuous
    flow of replies"): only the user's own turns get a background box;
    assistant turns are fully transparent/borderless so consecutive
    replies (interleaved with tool-call rows, images, etc.) read as one
    continuous flow rather than a stack of separate panels. Uses Qt's
    dynamic ``palette(...)`` roles rather than a hardcoded color so it
    still looks right in both light and dark system themes."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self._raw_text = ""
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        role_label = QLabel(role.capitalize(), self)
        role_label.setStyleSheet("font-weight: bold; color: gray; font-size: 10px;")
        header.addWidget(role_label)
        header.addStretch(1)
        # "code blocks monospaced with copy button" (PLAN.md), plus "is
        # there a way to add that nice 'copy content' button" (bug
        # report): a flat, borderless, low-visual-weight button — closer
        # to the small icon-style copy affordance other chat UIs use than
        # a boxy toolbar button — so it doesn't fight the "continuous
        # flow, no boxes" styling above. Copies the raw Markdown source
        # (code fences included), same v1 whole-message scope as before.
        self._copy_button = QPushButton("⧉ Copy", self)
        self._copy_button.setFlat(True)
        self._copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_button.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: gray; font-size: 10px; }"
            "QPushButton:hover { color: palette(text); text-decoration: underline; }"
        )
        self._copy_button.clicked.connect(self.copy_to_clipboard)
        header.addWidget(self._copy_button)
        layout.addLayout(header)

        self._view = _AutoHeightTextBrowser(self)
        self._view.setReadOnly(True)
        self._view.setOpenExternalLinks(True)
        layout.addWidget(self._view)

        if role == "user":
            self.setStyleSheet(
                "MessageBubble { background-color: palette(alternate-base); border-radius: 8px; }"
            )
            layout.setContentsMargins(10, 6, 10, 8)
        else:
            self.setStyleSheet("MessageBubble { background: transparent; border: none; }")
            layout.setContentsMargins(2, 4, 2, 4)

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self._raw_text)

    def set_text(self, text: str) -> None:
        self._raw_text = text
        self._view.setMarkdown(text)

    def append_delta(self, text: str) -> None:
        self._raw_text += text
        self._view.setMarkdown(self._raw_text)

    @property
    def text(self) -> str:
        return self._raw_text

    @property
    def rendered_plain_text(self) -> str:
        return self._view.toPlainText()


class ErrorBanner(QFrame):
    """One ``AgentError``, tagged with which layer failed — "diagnostics
    are a feature" (PLAN.md): the layer name is always visible, never just
    a bare message."""

    def __init__(self, *, layer: str, message: str, detail: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layer = layer
        self.message = message
        self.detail = detail
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #ffe5e5;")

        layout = QVBoxLayout(self)
        text = f"[{layer}] {message}"
        if detail:
            text += f" — {detail}"
        label = QLabel(text, self)
        label.setWordWrap(True)
        layout.addWidget(label)


class ChatPanel(QWidget):
    """A scrollable, append-only transcript of one conversation, built by
    feeding it ``AgentEvent``s (live) or ``Message``s (resumed history)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.addStretch(1)  # keeps turns pinned to the top as they grow downward
        self._scroll_area.setWidget(self._content)
        outer.addWidget(self._scroll_area)

        self._current_assistant_bubble: MessageBubble | None = None
        self._tool_rows: dict[str, ToolCallRow] = {}

    # --- internal helpers --------------------------------------------------

    def _append_widget(self, widget: QWidget) -> None:
        self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    @property
    def widget_count(self) -> int:
        """Number of turn/row/artifact/error widgets currently shown (not
        counting the trailing layout stretch) — mainly for tests."""
        return self._content_layout.count() - 1

    def widget_at(self, index: int) -> QWidget:
        return self._content_layout.itemAt(index).widget()

    # --- public API ----------------------------------------------------------

    def add_user_message(self, text: str) -> MessageBubble:
        bubble = MessageBubble("user", self._content)
        bubble.set_text(text)
        self._append_widget(bubble)
        return bubble

    def add_artifact_widget(self, widget: QWidget) -> None:
        """Append an already-built artifact widget (``InlineImageWidget``/
        ``FileArtifactCard``) directly — used by ``MainWindow`` when
        re-displaying a resumed conversation's artifacts from
        ``aida.persistence`` metadata rather than from a live
        ``ImageArtifactCreated``/``FileArtifactCreated`` event (see
        ``load_history``'s docstring)."""
        widget.setParent(self._content)
        self._append_widget(widget)

    def handle_event(self, event: object) -> None:
        """Dispatch one ``AgentEvent`` — mirrors
        ``aida.cli.chat.print_event``'s if/elif chain, one widget update per
        branch instead of one ``print()``."""
        name = type(event).__name__

        if name == "TextStarted":
            # Bug report: "if agent calls tools, I can see the call and
            # then empty box when agent keeps calling multiple tools... it
            # is impossible to follow." Real streaming providers always
            # emit TextStarted at the top of *every* assistant turn (e.g.
            # Anthropic's message_start), even a turn that's purely a tool
            # call with no text at all — TextFinished then arrives with
            # text="". Creating the bubble eagerly here meant a fresh empty
            # box appeared before every single tool call. Deferred to the
            # first TextDelta (or, failing that, TextFinished with actual
            # text) instead — a text-less turn now produces no bubble at
            # all, so multi-tool-call turns show only the tool rows.
            self._current_assistant_bubble = None
        elif name == "TextDelta":
            if self._current_assistant_bubble is None:
                self._current_assistant_bubble = MessageBubble("assistant", self._content)
                self._append_widget(self._current_assistant_bubble)
            self._current_assistant_bubble.append_delta(event.text)
            self._scroll_to_bottom()
        elif name == "TextFinished":
            if self._current_assistant_bubble is not None:
                self._current_assistant_bubble.set_text(event.text)
            elif event.text:
                # No TextDelta arrived (a non-streaming provider path) but
                # there's real final text — still show it, just not an
                # empty bubble for a text-less tool-call turn.
                bubble = MessageBubble("assistant", self._content)
                bubble.set_text(event.text)
                self._append_widget(bubble)
            self._current_assistant_bubble = None
        elif name == "ToolCallStarted":
            row = ToolCallRow(
                call_id=event.call_id, tool_name=event.tool_name, arguments=event.arguments, parent=self._content
            )
            self._tool_rows[event.call_id] = row
            self._append_widget(row)
        elif name == "ToolCallFinished":
            row = self._tool_rows.get(event.call_id)
            if row is not None:
                row.mark_finished(result=event.result, is_error=event.is_error)
        elif name == "ImageArtifactCreated":
            if event.path:
                widget = InlineImageWidget(
                    path=event.path, artifact_id=event.artifact_id, mime_type=event.mime_type, parent=self._content
                )
                self._append_widget(widget)
        elif name == "FileArtifactCreated":
            widget = FileArtifactCard(
                path=event.path, artifact_id=event.artifact_id, mime_type=event.mime_type, parent=self._content
            )
            self._append_widget(widget)
        elif name == "MessageFinished":
            pass  # no widget of its own — TextFinished/tool rows already reflect it
        elif name == "UsageInfo":
            pass  # not shown by default in v1 (matches the CLI); a future settings toggle could surface it
        elif name == "AgentError":
            banner = ErrorBanner(layer=event.layer, message=event.message, detail=event.detail, parent=self._content)
            self._append_widget(banner)

    def load_history(self, messages: Iterable[Message]) -> None:
        """Render already-persisted messages (resume path) directly, since
        the individual streaming/tool events that originally produced them
        aren't replayed — one bubble per user/assistant/tool message, in
        order. Artifacts referenced by a resumed tool message are shown as
        their text-policy description only in v1 (re-displaying resumed
        images inline is Phase 5 acceptance criterion "resume yesterday's
        conversation... images still display", covered by
        ``aida.ui.qt.main_window`` re-loading artifact rows from the
        conversation store, not by this method)."""
        for message in messages:
            if message.role == "system":
                continue
            bubble = MessageBubble(message.role, self._content)
            bubble.set_text(message.content or "")
            self._append_widget(bubble)

    def clear(self) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._current_assistant_bubble = None
        self._tool_rows.clear()


__all__ = ["ChatPanel", "ErrorBanner", "MessageBubble"]
