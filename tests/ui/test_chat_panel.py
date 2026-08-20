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
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
)
from aida.providers.base import Message
from aida.ui.qt._qt import QGuiApplication
from aida.ui.qt.artifact_widgets import FileArtifactCard, InlineImageWidget
from aida.ui.qt.chat_panel import ChatPanel, ErrorBanner, MessageBubble
from aida.ui.qt.tool_call_widget import ToolCallRow
from tests.mock_mcp_server import TINY_PNG_BYTES


def test_add_user_message_appends_bubble(qapp):
    panel = ChatPanel()
    panel.add_user_message("hello there")
    assert panel.widget_count == 1
    bubble = panel.widget_at(0)
    assert isinstance(bubble, MessageBubble)
    assert bubble.role == "user"
    assert "hello there" in bubble.rendered_plain_text


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
