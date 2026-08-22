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

import re
from collections.abc import Iterable
from datetime import datetime

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
    QTextCursor,
    QTimer,
    QVBoxLayout,
    QWidget,
    Signal,
)
from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from aida.ui.qt.retrieval_widget import RetrievalRow
from aida.ui.qt.tool_call_widget import ToolCallRow


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


_CODE_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

#: How often a streaming reply is re-rendered, at most.
#:
#: Every ``TextDelta`` used to call ``setMarkdown`` on the *whole*
#: accumulated text, which re-parses the entire document, re-runs the
#: code-fence regex over all of it, and fires ``documentSizeChanged`` ->
#: ``_recalculate_height``. That is O(n²) over a reply: a 4000-token answer
#: re-parsed the full document thousands of times, and the visible symptom
#: is the GUI getting progressively less responsive the longer the model
#: talks. Coalescing deltas onto one timer keeps the render cost
#: proportional to elapsed time rather than to token count, while still
#: looking live. ``set_text`` (``TextFinished``) always renders immediately,
#: so the finished message is never left waiting on a timer.
_STREAM_RENDER_INTERVAL_MS = 100


def _unwrap_preformatted_blocks(document: object) -> int:
    """Let fenced code blocks wrap at the widget width like everything else.

    Qt's Markdown importer marks a ``<pre>`` block's ``QTextBlockFormat``
    with ``nonBreakableLines``, which overrides every widget-level wrap
    setting there is — ``setWordWrapMode``, ``setLineWrapMode``,
    ``document().setDefaultTextOption(...)`` and a
    ``pre { white-space: pre-wrap }`` default stylesheet all leave it laid
    out at its natural width (measured: 701px of content in a 400px
    viewport, unchanged by any of them). With
    ``_AutoHeightTextBrowser``'s horizontal scrollbar forced off, that
    overflow is silently *clipped* — the tail of a long code line is
    unreachable. Clearing the flag per block is the only thing that
    actually rewraps it; ``toPlainText()`` is unaffected, so Copy and
    ``first_code_block`` still see the original source.

    Returns the number of blocks changed (mainly for tests)."""
    cursor = QTextCursor(document)
    cursor.beginEditBlock()  # one relayout + one undo step for the whole pass
    block = document.begin()
    changed = 0
    while block.isValid():
        block_format = block.blockFormat()
        if block_format.nonBreakableLines():
            block_format.setNonBreakableLines(False)
            QTextCursor(block).setBlockFormat(block_format)
            changed += 1
        block = block.next()
    cursor.endEditBlock()
    return changed


class _ConversationScrollArea(QScrollArea):
    """A ``QScrollArea`` whose content widget can never grow wider than the
    viewport.

    Bug report: "all text is not wrapped to current width of the chat
    window... there is a horizontal scroll control to let me move left and
    right." ``setWidgetResizable(True)`` grows the content widget to the
    viewport but never *shrinks* it below the content's
    ``minimumSizeHint()``. One over-wide row anywhere in the transcript —
    a tool-call summary whose longest token can't be broken, an artifact
    card's unwrapped filename — therefore widens the whole content widget,
    and since every ``MessageBubble`` is laid out at that inflated width,
    *every* message then wraps at it instead of at the visible width. The
    measured effect of a single bad row: content width 2006px in an 898px
    viewport, so each reply's text wrapped at 1984px and had to be scrolled
    to.

    Capping the content to the viewport fixes the whole class of problem at
    once, rather than chasing each widget that forgets ``setWordWrap(True)``
    — a word-wrapping child re-wraps to fit, and only a genuinely
    unbreakable child (a wide image) is clipped. Note that turning the
    horizontal scrollbar off alone does *not* work: it hides the scrollbar
    but leaves the content 2006px wide, so the text stays unwrapped and is
    now unreachable as well."""

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._clamp_content_width()

    def setWidget(self, widget: QWidget) -> None:  # noqa: N802 - Qt override
        super().setWidget(widget)
        self._clamp_content_width()

    def _clamp_content_width(self) -> None:
        content = self.widget()
        if content is not None:
            content.setMaximumWidth(self.viewport().width())


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

    #: Phase 9: "Code blocks in chat get 'Open in editor'". Carries the
    #: *first* fenced code block's content — same "v1 whole-message scope"
    #: simplification the Copy button already uses, not multi-block
    #: selection.
    code_editor_requested = Signal(str)

    def __init__(self, role: str, parent: QWidget | None = None, *, timestamp: str | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self._raw_text = ""
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        role_label = QLabel(role.capitalize(), self)
        role_label.setStyleSheet("font-weight: bold; color: gray; font-size: 10px;")
        header.addWidget(role_label)
        # Bug report: "Add time stamps to each message, may be tok/sec if
        # available and wallclock time." Live messages (add_user_message,
        # streamed assistant replies) pass a wall-clock HH:MM:SS at
        # creation; resumed history (load_history) passes nothing, since no
        # per-message timestamp is persisted yet (Message, the
        # provider-facing wire type, deliberately doesn't carry a GUI
        # display concern) — a blank label there rather than a misleading
        # "now". append_meta adds the tok/sec + duration suffix once a
        # UsageInfo event lands for this bubble (see ChatPanel.handle_event).
        self._meta_label = QLabel(timestamp or "", self)
        self._meta_label.setStyleSheet("color: gray; font-size: 10px;")
        header.addWidget(self._meta_label)
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
        # Phase 9: "Code blocks in chat get 'Open in editor'" — same flat/
        # borderless styling as Copy, hidden until the message actually
        # contains a fenced code block (see _update_open_in_editor_visibility).
        self._open_in_editor_button = QPushButton("</> Open in Editor", self)
        self._open_in_editor_button.setFlat(True)
        self._open_in_editor_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_in_editor_button.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: gray; font-size: 10px; }"
            "QPushButton:hover { color: palette(text); text-decoration: underline; }"
        )
        self._open_in_editor_button.setVisible(False)
        self._open_in_editor_button.clicked.connect(self._on_open_in_editor_clicked)
        header.addWidget(self._open_in_editor_button)
        layout.addLayout(header)

        self._view = _AutoHeightTextBrowser(self)
        self._view.setReadOnly(True)
        self._view.setOpenExternalLinks(True)
        layout.addWidget(self._view)

        # Coalesces streamed deltas into at most one render per
        # _STREAM_RENDER_INTERVAL_MS — see that constant.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_STREAM_RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render_now)

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

    def first_code_block(self) -> str | None:
        match = _CODE_FENCE_RE.search(self._raw_text)
        return match.group(1) if match else None

    def _on_open_in_editor_clicked(self) -> None:
        code = self.first_code_block()
        if code is not None:
            self.code_editor_requested.emit(code)

    def _update_open_in_editor_visibility(self) -> None:
        self._open_in_editor_button.setVisible(self.first_code_block() is not None)

    def set_text(self, text: str) -> None:
        """Set the message's full text and render it right away — the
        end-of-stream path, and the one resumed history/user messages use."""
        self._raw_text = text
        self._render_now()

    def append_delta(self, text: str) -> None:
        """Append one streamed delta. The text is recorded immediately (so
        ``text``/``copy_to_clipboard`` are always current); the *render* is
        coalesced onto ``_render_timer`` — see ``_STREAM_RENDER_INTERVAL_MS``."""
        self._raw_text += text
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render_now(self) -> None:
        self._render_timer.stop()
        self._view.setMarkdown(self._raw_text)
        _unwrap_preformatted_blocks(self._view.document())
        self._update_open_in_editor_visibility()

    def append_meta(self, text: str) -> None:
        """Adds a " · "-separated suffix to the header's timestamp label —
        used to attach the tok/sec + duration line once a UsageInfo event
        lands for this bubble."""
        current = self._meta_label.text()
        self._meta_label.setText(f"{current} · {text}" if current else text)

    @property
    def text(self) -> str:
        return self._raw_text

    @property
    def meta_text(self) -> str:
        """The header's timestamp (+ tok/sec suffix, once appended) —
        mainly for tests."""
        return self._meta_label.text()

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
        # Background is a fixed pale pink regardless of theme, so the text
        # color must be fixed too — leaving it to the palette renders
        # near-white-on-pink in dark mode. #7a1f1f is a dark red readable on
        # #ffe5e5 in both light and dark themes; "color" is inherited by the
        # child QLabel via Qt's stylesheet cascade.
        self.setStyleSheet("background-color: #ffe5e5; color: #7a1f1f;")

        layout = QVBoxLayout(self)
        text = f"[{layer}] {message}"
        if detail:
            text += f" — {detail}"
        label = QLabel(text, self)
        label.setWordWrap(True)
        layout.addWidget(label)


class TruncationNotice(QFrame):
    """Shown when ``MessageFinished.stop_reason == "length"`` — the reply
    was cut off mid-sentence by ``max_tokens`` with no other indication why
    (bug report: truncated replies are silent). Styled as an unobtrusive
    warning rather than ``ErrorBanner``'s red, since this isn't a failure —
    the turn otherwise succeeded."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #fff3cd; color: #664d03;")
        layout = QVBoxLayout(self)
        label = QLabel(
            "Reply hit the max-tokens limit and was cut off — raise max_tokens in the profile settings.",
            self,
        )
        label.setWordWrap(True)
        layout.addWidget(label)


class ChatPanel(QWidget):
    """A scrollable, append-only transcript of one conversation, built by
    feeding it ``AgentEvent``s (live) or ``Message``s (resumed history)."""

    #: Re-emitted from whichever MessageBubble's own "Open in Editor"
    #: button was clicked — MainWindow connects this to
    #: open_code_editor_dialog, one connection instead of one per bubble.
    code_editor_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll_area = _ConversationScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.addStretch(1)  # keeps turns pinned to the top as they grow downward
        self._scroll_area.setWidget(self._content)
        outer.addWidget(self._scroll_area)

        self._current_assistant_bubble: MessageBubble | None = None
        # Kept alive past TextFinished resetting _current_assistant_bubble
        # to None — a UsageInfo event for this round-trip arrives right
        # after TextFinished, and still needs a bubble to attach its
        # tok/sec + duration line to (see handle_event's UsageInfo branch).
        self._last_assistant_bubble: MessageBubble | None = None
        self._tool_rows: dict[str, ToolCallRow] = {}

    # --- internal helpers --------------------------------------------------

    def _append_widget(self, widget: QWidget) -> None:
        self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _relay_code_editor_requests(self, bubble: MessageBubble) -> None:
        bubble.code_editor_requested.connect(self.code_editor_requested.emit)

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
        bubble = MessageBubble("user", self._content, timestamp=_now_str())
        bubble.set_text(text)
        self._relay_code_editor_requests(bubble)
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
                self._current_assistant_bubble = MessageBubble("assistant", self._content, timestamp=_now_str())
                self._relay_code_editor_requests(self._current_assistant_bubble)
                self._append_widget(self._current_assistant_bubble)
            self._current_assistant_bubble.append_delta(event.text)
            self._scroll_to_bottom()
        elif name == "TextFinished":
            if self._current_assistant_bubble is not None:
                self._current_assistant_bubble.set_text(event.text)
                self._last_assistant_bubble = self._current_assistant_bubble
            elif event.text:
                # No TextDelta arrived (a non-streaming provider path) but
                # there's real final text — still show it, just not an
                # empty bubble for a text-less tool-call turn.
                bubble = MessageBubble("assistant", self._content, timestamp=_now_str())
                bubble.set_text(event.text)
                self._relay_code_editor_requests(bubble)
                self._append_widget(bubble)
                self._last_assistant_bubble = bubble
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
        elif name == "RetrievalPerformed":
            row = RetrievalRow(passages_by_kb=event.passages_by_kb, parent=self._content)
            self._append_widget(row)
        elif name == "MessageFinished":
            if event.stop_reason == "length":
                self._append_widget(TruncationNotice(parent=self._content))
        elif name == "UsageInfo":
            # Bug report: "Add time stamps to each message, may be tok/sec
            # if available and wallclock time." Attaches to the bubble that
            # just finished (TextFinished, right before this, already reset
            # _current_assistant_bubble to None — see _last_assistant_bubble's
            # docstring). A tool-call-only round has no bubble to attach to;
            # its tokens still count toward MainWindow's running-total label.
            if self._last_assistant_bubble is not None and event.output_tokens and event.duration_seconds:
                rate = event.output_tokens / event.duration_seconds
                self._last_assistant_bubble.append_meta(
                    f"{event.output_tokens} tok · {event.duration_seconds:.1f}s · {rate:.1f} tok/s"
                )
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
            self._relay_code_editor_requests(bubble)
            self._append_widget(bubble)

    def clear(self) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._current_assistant_bubble = None
        self._last_assistant_bubble = None
        self._tool_rows.clear()


__all__ = ["ChatPanel", "ErrorBanner", "MessageBubble"]
