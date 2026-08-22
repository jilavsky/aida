"""Tests for aida.ui.qt.chat_panel.ChatPanel — driven directly with real
``aida.core.events.AgentEvent`` values (no bridge/session needed; the panel
only ever consumes events, same contract ChatBridge.event_received hands
it)."""

from __future__ import annotations

from pathlib import Path

from aida.core.events import (
    AgentError,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    RetrievalPerformed,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)
from aida.persistence.store import ArtifactRecord
from aida.providers.base import Message, ToolCall
from aida.ui.qt._qt import QGuiApplication
from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from aida.ui.qt.chat_panel import ChatPanel, ErrorBanner, MessageBubble
from aida.ui.qt.retrieval_widget import RetrievalRow
from aida.ui.qt.tool_call_widget import ToolCallRow
from tests.mock_mcp_server import TINY_PNG_BYTES
from tests.ui._qt_test_utils import pump_until


def test_add_user_message_appends_bubble(qapp):
    panel = ChatPanel()
    panel.add_user_message("hello there")
    assert panel.widget_count == 1
    bubble = panel.widget_at(0)
    assert isinstance(bubble, MessageBubble)
    assert bubble.role == "user"
    assert "hello there" in bubble.rendered_plain_text


def test_add_user_message_shows_a_timestamp(qapp):
    """Bug report: "Add time stamps to each message, may be tok/sec if
    available and wallclock time.\""""
    panel = ChatPanel()
    bubble = panel.add_user_message("hello there")
    assert bubble.meta_text != ""


def test_streamed_assistant_bubble_shows_a_timestamp(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="hello"))
    panel.handle_event(TextFinished(message_id="m1", text="hello"))
    bubble = panel.widget_at(0)
    assert bubble.meta_text != ""


def test_resumed_history_bubble_has_no_timestamp(qapp):
    """Deliberate scoping limit: per-message timestamps aren't persisted
    yet (Message, the provider-facing wire type, doesn't carry one) — a
    resumed bubble shows a blank meta label rather than a misleading
    "now"."""
    panel = ChatPanel()
    panel.load_history([Message(role="user", content="hi")])
    bubble = panel.widget_at(0)
    assert bubble.meta_text == ""


def test_usage_info_appends_tokens_per_second_to_the_last_assistant_bubble(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="hello"))
    panel.handle_event(TextFinished(message_id="m1", text="hello"))
    panel.handle_event(MessageFinished(message_id="m1", stop_reason="stop"))
    panel.handle_event(UsageInfo(input_tokens=100, output_tokens=50, duration_seconds=2.0))

    bubble = panel.widget_at(0)
    assert "50 tok" in bubble.meta_text
    assert "25.0 tok/s" in bubble.meta_text


def test_streamed_bubble_with_a_code_fence_shows_open_in_editor_button(qapp):
    """Bug report/phase task: "Code blocks in chat get 'Open in editor'"."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="Here:\n```python\nprint(1)\n```\n"))
    panel.handle_event(TextFinished(message_id="m1", text="Here:\n```python\nprint(1)\n```\n"))
    bubble = panel.widget_at(0)
    assert not bubble._open_in_editor_button.isHidden()


def test_plain_text_bubble_has_no_open_in_editor_button(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="just plain text"))
    panel.handle_event(TextFinished(message_id="m1", text="just plain text"))
    bubble = panel.widget_at(0)
    assert bubble._open_in_editor_button.isHidden()


def test_clicking_open_in_editor_emits_the_first_code_blocks_content(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(
        TextDelta(message_id="m1", text="```python\nprint('a')\n```\nand\n```python\nprint('b')\n```\n")
    )
    panel.handle_event(
        TextFinished(message_id="m1", text="```python\nprint('a')\n```\nand\n```python\nprint('b')\n```\n")
    )
    bubble = panel.widget_at(0)

    requested = []
    panel.code_editor_requested.connect(requested.append)
    bubble._open_in_editor_button.click()

    assert requested == ["print('a')\n"]


def test_user_message_with_code_fence_also_shows_the_button(qapp):
    panel = ChatPanel()
    bubble = panel.add_user_message("```python\nprint(1)\n```")
    assert not bubble._open_in_editor_button.isHidden()


def test_resumed_history_bubble_with_code_fence_shows_the_button_and_relays(qapp):
    panel = ChatPanel()
    panel.load_history([Message(role="assistant", content="```python\nprint(1)\n```")])
    bubble = panel.widget_at(0)
    assert not bubble._open_in_editor_button.isHidden()

    requested = []
    panel.code_editor_requested.connect(requested.append)
    bubble._open_in_editor_button.click()
    assert requested == ["print(1)\n"]


def test_usage_info_with_no_bubble_does_not_raise(qapp):
    """A tool-call-only round has no visible bubble to attach to — must be
    a no-op, not a crash."""
    panel = ChatPanel()
    panel.handle_event(UsageInfo(input_tokens=10, output_tokens=5, duration_seconds=1.0))
    assert panel.widget_count == 0


def test_message_bubble_copy_button_copies_raw_text_to_clipboard(qapp):
    panel = ChatPanel()
    bubble = panel.add_user_message("some `code` here")
    bubble.copy_to_clipboard()
    assert QGuiApplication.clipboard().text() == "some `code` here"


def test_streaming_text_builds_one_assistant_bubble(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="hel"))
    panel.handle_event(TextDelta(message_id="m1", text="lo"))
    panel.handle_event(TextFinished(message_id="m1", text="hello"))
    panel.handle_event(MessageFinished(message_id="m1", stop_reason="stop"))

    assert panel.widget_count == 1
    bubble = panel.widget_at(0)
    assert isinstance(bubble, MessageBubble)
    assert bubble.role == "assistant"
    assert bubble.text == "hello"


def test_tool_call_started_then_finished_updates_same_row(qapp):
    panel = ChatPanel()
    panel.handle_event(ToolCallStarted(call_id="c1", tool_name="get_time", arguments={"tz": "utc"}))
    assert panel.widget_count == 1
    row = panel.widget_at(0)
    assert isinstance(row, ToolCallRow)
    assert row.is_error is None

    panel.handle_event(ToolCallFinished(call_id="c1", tool_name="get_time", result="now", is_error=False))
    assert panel.widget_count == 1  # same row updated, not a second widget
    assert row.is_error is False


# --- bug report: no empty assistant bubble for a text-less tool-call turn -


def test_text_less_tool_call_turn_produces_no_empty_bubble(qapp):
    """Real streaming providers (e.g. Anthropic) emit TextStarted at the
    top of every turn, even a turn that's purely a tool call — followed by
    TextFinished(text=""). Previously this created an empty MessageBubble
    before every single tool call; multiple tool calls in a row produced a
    confusing string of empty boxes interleaved with the tool rows."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(ToolCallStarted(call_id="c1", tool_name="get_time", arguments={}))
    panel.handle_event(TextFinished(message_id="m1", text=""))
    panel.handle_event(MessageFinished(message_id="m1", stop_reason="tool_calls"))
    panel.handle_event(ToolCallFinished(call_id="c1", tool_name="get_time", result="now", is_error=False))

    panel.handle_event(TextStarted(message_id="m2"))
    panel.handle_event(ToolCallStarted(call_id="c2", tool_name="get_date", arguments={}))
    panel.handle_event(TextFinished(message_id="m2", text=""))
    panel.handle_event(MessageFinished(message_id="m2", stop_reason="tool_calls"))
    panel.handle_event(ToolCallFinished(call_id="c2", tool_name="get_date", result="today", is_error=False))

    kinds = [type(panel.widget_at(i)).__name__ for i in range(panel.widget_count)]
    assert kinds == ["ToolCallRow", "ToolCallRow"]  # no MessageBubble at all


def test_text_finished_with_text_but_no_deltas_still_shows_a_bubble(qapp):
    """Belt-and-suspenders for a hypothetical non-streaming provider path:
    if TextFinished ever arrives with real text but no TextDelta preceded
    it, that text must still be shown — only a genuinely empty turn should
    produce nothing."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextFinished(message_id="m1", text="final answer, no deltas"))

    assert panel.widget_count == 1
    bubble = panel.widget_at(0)
    assert isinstance(bubble, MessageBubble)
    assert bubble.text == "final answer, no deltas"


# --- bug report: borderless/continuous-flow styling, auto-height text -----


def test_user_bubble_has_a_background_assistant_bubble_does_not(qapp):
    panel = ChatPanel()
    user_bubble = panel.add_user_message("hi")
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="hello"))
    panel.handle_event(TextFinished(message_id="m1", text="hello"))
    assistant_bubble = panel.widget_at(1)

    assert "background-color" in user_bubble.styleSheet()
    assert "background-color" not in assistant_bubble.styleSheet()
    assert "background: transparent" in assistant_bubble.styleSheet()


def test_message_bubble_text_view_has_no_frame_or_internal_scrollbar(qapp):
    from aida.ui.qt._qt import QFrame, Qt

    panel = ChatPanel()
    bubble = panel.add_user_message("hi")
    assert bubble._view.frameShape() == QFrame.Shape.NoFrame
    assert bubble._view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_message_bubble_view_grows_taller_for_more_content(qapp):
    panel = ChatPanel()
    short_bubble = panel.add_user_message("one line")
    long_bubble = panel.add_user_message("line\n" * 40)

    # Force a layout pass so the auto-height recalculation (tied to the
    # document's layout, which needs a real width) has taken effect.
    panel.show()
    qapp.processEvents()

    assert long_bubble._view.height() > short_bubble._view.height()


def test_image_artifact_created_adds_inline_image(qapp, tmp_path: Path):
    png_path = tmp_path / "plot.png"
    png_path.write_bytes(TINY_PNG_BYTES)

    panel = ChatPanel()
    panel.handle_event(
        ImageArtifactCreated(artifact_id="a1", call_id="c1", mime_type="image/png", path=str(png_path))
    )
    assert panel.widget_count == 1
    widget = panel.widget_at(0)
    assert isinstance(widget, InlineImageWidget)
    assert widget.is_valid_image


def test_file_artifact_created_adds_file_card(qapp, tmp_path: Path):
    file_path = tmp_path / "report.md"
    file_path.write_text("# report", encoding="utf-8")

    panel = ChatPanel()
    panel.handle_event(FileArtifactCreated(artifact_id="a1", call_id="c1", path=str(file_path), mime_type="text/markdown"))
    assert panel.widget_count == 1
    assert isinstance(panel.widget_at(0), FileArtifactCard)


def test_agent_error_adds_error_banner_with_layer(qapp):
    panel = ChatPanel()
    panel.handle_event(AgentError(layer="provider", message="boom", detail="net down"))
    assert panel.widget_count == 1
    banner = panel.widget_at(0)
    assert isinstance(banner, ErrorBanner)
    assert banner.layer == "provider"
    assert banner.message == "boom"


def test_full_turn_with_tool_call_and_image_produces_expected_widget_sequence(qapp, tmp_path: Path):
    """A realistic turn, in the exact order aida.core.agent.AgentLoop emits
    them (see test_keystone_image_roundtrip.py) -> user bubble, assistant
    text bubble (the "let me get that" preamble), tool row, inline image,
    then a second assistant bubble for the final reply."""
    png_path = tmp_path / "plot.png"
    png_path.write_bytes(TINY_PNG_BYTES)

    panel = ChatPanel()
    panel.add_user_message("plot dataset X")

    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="let me get that"))
    panel.handle_event(TextFinished(message_id="m1", text="let me get that"))
    panel.handle_event(ToolCallStarted(call_id="c1", tool_name="mock-mcp.get_image", arguments={}))
    panel.handle_event(MessageFinished(message_id="m1", stop_reason="tool_calls"))
    panel.handle_event(ToolCallFinished(call_id="c1", tool_name="mock-mcp.get_image", result="image/png", is_error=False))
    panel.handle_event(ImageArtifactCreated(artifact_id="a1", call_id="c1", mime_type="image/png", path=str(png_path)))

    panel.handle_event(TextStarted(message_id="m2"))
    panel.handle_event(TextDelta(message_id="m2", text="here it is"))
    panel.handle_event(TextFinished(message_id="m2", text="here it is"))
    panel.handle_event(MessageFinished(message_id="m2", stop_reason="stop"))

    kinds = [type(panel.widget_at(i)).__name__ for i in range(panel.widget_count)]
    assert kinds == ["MessageBubble", "MessageBubble", "ToolCallRow", "InlineImageWidget", "MessageBubble"]
    assert panel.widget_at(1).text == "let me get that"
    assert panel.widget_at(4).text == "here it is"


def test_load_history_renders_one_bubble_per_message_skipping_system(qapp):
    panel = ChatPanel()
    panel.load_history(
        [
            Message(role="system", content="you are helpful"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello!"),
        ]
    )
    assert panel.widget_count == 2
    assert panel.widget_at(0).role == "user"
    assert panel.widget_at(1).role == "assistant"


# --- U6: resumed tool messages render as collapsed rows, and artifacts
# interleave at their original position ------------------------------------


def test_load_history_renders_resumed_tool_message_as_a_collapsed_row(qapp):
    """Bug report: a resumed analysis session "replays as a wall of raw
    tool output" — a role="tool" message used to render as a full text
    bubble. A tool-call-only assistant turn (no text) produces no bubble
    either, matching the live TextStarted-deferred behavior."""
    panel = ChatPanel()
    panel.load_history(
        [
            Message(role="user", content="what time is it?"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call_1", name="get_current_time", arguments={"tz": "utc"})],
            ),
            Message(role="tool", content="the time is now", tool_call_id="call_1", name="get_current_time"),
            Message(role="assistant", content="it's noon"),
        ]
    )
    kinds = [type(panel.widget_at(i)).__name__ for i in range(panel.widget_count)]
    assert kinds == ["MessageBubble", "ToolCallRow", "MessageBubble"]

    row = panel.widget_at(1)
    assert row.tool_name == "get_current_time"
    assert row.arguments == {"tz": "utc"}
    assert row.is_error is None
    assert "the time is now" in row._detail_text.toPlainText()
    assert panel.widget_at(2).text == "it's noon"


def test_load_history_tool_row_with_unmatched_call_id_gets_empty_arguments(qapp):
    """A tool message whose matching assistant tool_calls entry isn't in
    this history (e.g. it was trimmed) must not raise — just show no
    recovered arguments."""
    panel = ChatPanel()
    panel.load_history([Message(role="tool", content="ok", tool_call_id="call_missing", name="a_tool")])
    row = panel.widget_at(0)
    assert row.arguments == {}


def test_load_history_interleaves_artifacts_at_their_recorded_seq(qapp, tmp_path: Path):
    png_path = tmp_path / "plot.png"
    png_path.write_bytes(TINY_PNG_BYTES)
    record = ArtifactRecord(
        id="a1", conversation_id="c1", call_id="call_1", kind="ImageArtifact",
        path=str(png_path), mime_type="image/png", created_at="2026-08-22T00:00:00", seq=2,
    )
    messages = [
        Message(role="user", content="plot it"),
        Message(
            role="assistant", content="", tool_calls=[ToolCall(id="call_1", name="get_plot", arguments={})]
        ),
        Message(role="tool", content="[image]", tool_call_id="call_1", name="get_plot"),
        Message(role="assistant", content="here it is"),
    ]

    panel = ChatPanel()
    panel.load_history(messages, seqs=[0, 1, 2, 3], artifacts_by_seq={2: [record]})

    kinds = [type(panel.widget_at(i)).__name__ for i in range(panel.widget_count)]
    # user bubble, tool row (seq2's artifact right after it), image, final reply
    assert kinds == ["MessageBubble", "ToolCallRow", "InlineImageWidget", "MessageBubble"]


def test_load_history_artifact_with_a_missing_file_is_skipped(qapp, tmp_path: Path):
    record = ArtifactRecord(
        id="a1", conversation_id="c1", call_id="call_1", kind="ImageArtifact",
        path=str(tmp_path / "gone.png"), mime_type="image/png", created_at="2026-08-22T00:00:00", seq=0,
    )
    panel = ChatPanel()
    panel.load_history([Message(role="user", content="hi")], seqs=[0], artifacts_by_seq={0: [record]})
    assert panel.widget_count == 1  # just the user bubble — the missing-file artifact was skipped


def test_load_history_without_seqs_still_works_exactly_as_before(qapp):
    """seqs/artifacts_by_seq are both optional — a caller that only passes
    messages (every existing caller, and any test that predates U6) must
    see unchanged behavior."""
    panel = ChatPanel()
    panel.load_history([Message(role="user", content="hi"), Message(role="assistant", content="hello!")])
    assert panel.widget_count == 2


def test_artifact_widget_for_builds_the_right_widget_kind(qapp, tmp_path: Path):
    image_path = tmp_path / "plot.png"
    image_path.write_bytes(TINY_PNG_BYTES)
    file_path = tmp_path / "report.md"
    file_path.write_text("# report", encoding="utf-8")
    panel = ChatPanel()

    image_widget = panel.artifact_widget_for(
        ArtifactRecord(
            id="a1", conversation_id="c1", call_id=None, kind="ImageArtifact",
            path=str(image_path), mime_type="image/png", created_at="2026-08-22T00:00:00",
        )
    )
    assert isinstance(image_widget, InlineImageWidget)

    file_widget = panel.artifact_widget_for(
        ArtifactRecord(
            id="a2", conversation_id="c1", call_id=None, kind="FileArtifact",
            path=str(file_path), mime_type="text/markdown", created_at="2026-08-22T00:00:00",
        )
    )
    assert isinstance(file_widget, FileArtifactCard)

    unknown_widget = panel.artifact_widget_for(
        ArtifactRecord(
            id="a3", conversation_id="c1", call_id=None, kind="TextArtifact",
            path=None, mime_type=None, created_at="2026-08-22T00:00:00",
        )
    )
    assert unknown_widget is None


def test_retrieval_performed_renders_a_retrieval_row(qapp):
    panel = ChatPanel()
    panel.handle_event(
        RetrievalPerformed(
            passages_by_kb={
                "usaxs-docs": [
                    {"text": "Unified Fit models a SAXS curve.", "source_path": "/docs/fit.md", "heading": "Fitting", "score": 0.82}
                ]
            }
        )
    )
    assert panel.widget_count == 1
    row = panel.widget_at(0)
    assert isinstance(row, RetrievalRow)
    assert not row.is_expanded
    assert "Retrieved 1 passage(s) from 1 knowledge base(s)" in row._summary_label.text()

    row.toggle_expanded()
    assert row.is_expanded
    assert "Unified Fit models a SAXS curve." in row._detail_text.toPlainText()
    assert "usaxs-docs" in row._detail_text.toPlainText()


def test_clear_removes_all_widgets_and_resets_state(qapp):
    panel = ChatPanel()
    panel.add_user_message("hi")
    panel.handle_event(ToolCallStarted(call_id="c1", tool_name="t", arguments={}))
    assert panel.widget_count == 2

    panel.clear()
    qapp.processEvents()  # deleteLater() needs an event loop turn to actually take effect
    assert panel.widget_count == 0

    # panel is still usable after clear()
    panel.add_user_message("again")
    assert panel.widget_count == 1


# --- streaming a long reply must not re-render per token -------------------
#
# Review finding: every TextDelta called setMarkdown() on the whole
# accumulated text, re-parsing the entire document, re-running the
# code-fence regex over all of it, and triggering documentSizeChanged ->
# _recalculate_height. That's O(n²) over a reply — a 4000-token answer
# re-parsed the full document thousands of times, and the GUI got
# progressively less responsive the longer the model talked.


def test_streaming_deltas_are_coalesced_into_few_renders(qapp):
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="start "))
    bubble = panel.widget_at(0)

    renders = []
    real_set_markdown = bubble._view.setMarkdown
    bubble._view.setMarkdown = lambda text: (renders.append(text), real_set_markdown(text))[1]

    for i in range(200):
        panel.handle_event(TextDelta(message_id="m1", text=f"token{i} "))

    assert len(renders) <= 2, f"{len(renders)} renders for 200 deltas — deltas are not being coalesced"


def test_the_raw_text_is_always_current_even_before_a_render(qapp):
    """Coalescing the *render* must not delay the text itself — Copy and
    the persisted history read from it."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="hel"))
    panel.handle_event(TextDelta(message_id="m1", text="lo"))

    assert panel.widget_at(0).text == "hello"


def test_text_finished_renders_immediately(qapp):
    """The end of a stream must never be left waiting on a timer."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="par"))
    panel.handle_event(TextDelta(message_id="m1", text="tial"))
    panel.handle_event(TextFinished(message_id="m1", text="partial answer"))

    assert "partial answer" in panel.widget_at(0).rendered_plain_text


def test_a_pending_render_still_lands_when_the_timer_fires(qapp):
    """A stream that stalls mid-reply (a slow provider) must still show what
    arrived so far, without waiting for TextFinished."""
    panel = ChatPanel()
    panel.handle_event(TextStarted(message_id="m1"))
    panel.handle_event(TextDelta(message_id="m1", text="streamed so far"))
    bubble = panel.widget_at(0)

    assert pump_until(qapp, lambda: "streamed so far" in bubble.rendered_plain_text)
