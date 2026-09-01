"""Translation-layer tests for both provider dialects (Phase 2 requirement:
"Provider dialect translation tests (schema in/out) for both SDKs").

These test the pure functions (``process_openai_chunk``,
``process_anthropic_event``, and the message/tool converters) directly
against real SDK-typed objects, rather than mocking HTTP transport — see the
module docstrings in ``openai_compat.py``/``anthropic_.py`` for why that
split makes this possible without a network layer at all.
"""

from __future__ import annotations

from aida.providers.base import CompletionSettings, ImageRef, Message, ToolCall, ToolSchema

# ---------------------------------------------------------------------------
# openai_compat
# ---------------------------------------------------------------------------


def test_to_openai_messages_plain_roles():
    from aida.providers.openai_compat import to_openai_messages

    messages = [
        Message(role="system", content="be helpful"),
        Message(role="user", content="hi"),
    ]
    out = to_openai_messages(messages)
    assert out == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]


def test_to_openai_messages_assistant_tool_call():
    from aida.providers.openai_compat import to_openai_messages

    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="get_time", arguments={"tz": "utc"})],
        )
    ]
    out = to_openai_messages(messages)
    assert out[0]["role"] == "assistant"
    assert out[0]["tool_calls"][0]["id"] == "call_1"
    assert out[0]["tool_calls"][0]["function"]["name"] == "get_time"
    assert '"tz"' in out[0]["tool_calls"][0]["function"]["arguments"]


def test_to_openai_messages_tool_result():
    from aida.providers.openai_compat import to_openai_messages

    messages = [Message(role="tool", content="42", tool_call_id="call_1", name="get_time")]
    out = to_openai_messages(messages)
    assert out == [{"role": "tool", "tool_call_id": "call_1", "content": "42"}]


def test_to_openai_tools_empty_is_none():
    from aida.providers.openai_compat import to_openai_tools

    assert to_openai_tools([]) is None


def test_to_openai_tools_shape():
    from aida.providers.openai_compat import to_openai_tools

    schema = ToolSchema(name="get_time", description="Get time", parameters={"type": "object"})
    out = to_openai_tools([schema])
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "Get time",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_process_openai_chunk_text_streaming():
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
    from openai.types.completion_usage import CompletionUsage

    from aida.core.events import MessageFinished, TextDelta, TextFinished, TextStarted, UsageInfo
    from aida.providers.openai_compat import _StreamState, process_openai_chunk

    state = _StreamState(message_id="m1")
    events = []

    def chunk(**kw):
        return ChatCompletionChunk(id="1", created=0, model="x", object="chat.completion.chunk", **kw)

    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(content="Hello "), finish_reason=None)]),
        state,
    )
    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(content="world"), finish_reason=None)]),
        state,
    )
    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(), finish_reason="stop")]), state
    )
    events += process_openai_chunk(
        chunk(
            choices=[],
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        ),
        state,
    )

    assert isinstance(events[0], TextStarted)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello ", "world"]
    finished = next(e for e in events if isinstance(e, TextFinished))
    assert finished.text == "Hello world"
    msg_finished = next(e for e in events if isinstance(e, MessageFinished))
    assert msg_finished.stop_reason == "stop"
    usage = next(e for e in events if isinstance(e, UsageInfo))
    assert usage.input_tokens == 5
    assert usage.output_tokens == 2


def test_process_openai_chunk_tool_call_accumulates_streamed_arguments():
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import (
        Choice,
        ChoiceDelta,
        ChoiceDeltaToolCall,
        ChoiceDeltaToolCallFunction,
    )

    from aida.core.events import ToolCallStarted
    from aida.providers.openai_compat import _StreamState, process_openai_chunk

    state = _StreamState(message_id="m2")
    events = []

    def chunk(**kw):
        return ChatCompletionChunk(id="2", created=0, model="x", object="chat.completion.chunk", **kw)

    tc1 = ChoiceDeltaToolCall(
        index=0,
        id="call_1",
        type="function",
        function=ChoiceDeltaToolCallFunction(name="get_current_time", arguments=""),
    )
    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(tool_calls=[tc1]), finish_reason=None)]),
        state,
    )
    tc2 = ChoiceDeltaToolCall(index=0, function=ChoiceDeltaToolCallFunction(arguments='{"tz": "utc"}'))
    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(tool_calls=[tc2]), finish_reason=None)]),
        state,
    )
    events += process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(), finish_reason="tool_calls")]), state
    )

    call = next(e for e in events if isinstance(e, ToolCallStarted))
    assert call.call_id == "call_1"
    assert call.tool_name == "get_current_time"
    assert call.arguments == {"tz": "utc"}


# ---------------------------------------------------------------------------
# anthropic_
# ---------------------------------------------------------------------------


def test_to_anthropic_params_splits_system_message():
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [
        Message(role="system", content="be helpful"),
        Message(role="user", content="hi"),
    ]
    system, out = to_anthropic_params(messages)
    assert system == "be helpful"
    assert out == [{"role": "user", "content": "hi"}]


def test_to_anthropic_params_assistant_tool_call():
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [
        Message(
            role="assistant",
            content="checking...",
            tool_calls=[ToolCall(id="toolu_1", name="get_time", arguments={"tz": "utc"})],
        )
    ]
    _system, out = to_anthropic_params(messages)
    content = out[0]["content"]
    assert content[0] == {"type": "text", "text": "checking..."}
    assert content[1] == {"type": "tool_use", "id": "toolu_1", "name": "get_time", "input": {"tz": "utc"}}


def test_to_anthropic_params_tool_result():
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [Message(role="tool", content="42", tool_call_id="toolu_1")]
    _system, out = to_anthropic_params(messages)
    assert out == [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"}],
        }
    ]


def test_to_anthropic_tools_shape():
    from aida.providers.anthropic_ import to_anthropic_tools

    schema = ToolSchema(name="get_time", description="Get time", parameters={"type": "object"})
    out = to_anthropic_tools([schema])
    assert out == [{"name": "get_time", "description": "Get time", "input_schema": {"type": "object"}}]


def test_process_anthropic_event_text_streaming():
    from anthropic.types import (
        Message as AnthropicMessage,
    )
    from anthropic.types import (
        RawContentBlockDeltaEvent,
        RawContentBlockStartEvent,
        RawContentBlockStopEvent,
        RawMessageDeltaEvent,
        RawMessageStartEvent,
        RawMessageStopEvent,
        TextBlock,
        Usage,
    )
    from anthropic.types import (
        TextDelta as AnthropicTextDelta,
    )
    from anthropic.types.raw_message_delta_event import Delta as MsgDelta

    from aida.core.events import MessageFinished, TextDelta, TextFinished, TextStarted, UsageInfo
    from aida.providers.anthropic_ import _StreamState, process_anthropic_event

    state = _StreamState(message_id="m1")
    events = []

    msg = AnthropicMessage(
        id="msg_1",
        content=[],
        model="claude-x",
        role="assistant",
        stop_reason=None,
        type="message",
        usage=Usage(input_tokens=10, output_tokens=0),
    )
    events += process_anthropic_event(RawMessageStartEvent(type="message_start", message=msg), state)
    events += process_anthropic_event(
        RawContentBlockStartEvent(type="content_block_start", index=0, content_block=TextBlock(type="text", text="")),
        state,
    )
    events += process_anthropic_event(
        RawContentBlockDeltaEvent(
            type="content_block_delta", index=0, delta=AnthropicTextDelta(type="text_delta", text="Hello ")
        ),
        state,
    )
    events += process_anthropic_event(
        RawContentBlockDeltaEvent(
            type="content_block_delta", index=0, delta=AnthropicTextDelta(type="text_delta", text="world")
        ),
        state,
    )
    events += process_anthropic_event(RawContentBlockStopEvent(type="content_block_stop", index=0), state)

    from anthropic.types import MessageDeltaUsage

    events += process_anthropic_event(
        RawMessageDeltaEvent(
            type="message_delta",
            delta=MsgDelta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=5),
        ),
        state,
    )
    events += process_anthropic_event(RawMessageStopEvent(type="message_stop"), state)

    assert isinstance(events[0], TextStarted)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello ", "world"]
    finished = next(e for e in events if isinstance(e, TextFinished))
    assert finished.text == "Hello world"
    msg_finished = next(e for e in events if isinstance(e, MessageFinished))
    assert msg_finished.stop_reason == "stop"
    usage = next(e for e in events if isinstance(e, UsageInfo))
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


def test_process_anthropic_event_tool_use_accumulates_partial_json():
    from anthropic.types import (
        InputJSONDelta,
        MessageDeltaUsage,
        RawContentBlockDeltaEvent,
        RawContentBlockStartEvent,
        RawContentBlockStopEvent,
        RawMessageDeltaEvent,
        RawMessageStartEvent,
        RawMessageStopEvent,
        ToolUseBlock,
        Usage,
    )
    from anthropic.types import (
        Message as AnthropicMessage,
    )
    from anthropic.types.raw_message_delta_event import Delta as MsgDelta

    from aida.core.events import ToolCallStarted
    from aida.providers.anthropic_ import _StreamState, process_anthropic_event

    state = _StreamState(message_id="m2")
    events = []

    msg = AnthropicMessage(
        id="msg_2",
        content=[],
        model="claude-x",
        role="assistant",
        stop_reason=None,
        type="message",
        usage=Usage(input_tokens=3, output_tokens=0),
    )
    events += process_anthropic_event(RawMessageStartEvent(type="message_start", message=msg), state)
    events += process_anthropic_event(
        RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock(type="tool_use", id="toolu_1", name="get_current_time", input={}),
        ),
        state,
    )
    events += process_anthropic_event(
        RawContentBlockDeltaEvent(
            type="content_block_delta", index=0, delta=InputJSONDelta(type="input_json_delta", partial_json='{"tz"')
        ),
        state,
    )
    events += process_anthropic_event(
        RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta(type="input_json_delta", partial_json=': "utc"}'),
        ),
        state,
    )
    events += process_anthropic_event(RawContentBlockStopEvent(type="content_block_stop", index=0), state)
    events += process_anthropic_event(
        RawMessageDeltaEvent(
            type="message_delta",
            delta=MsgDelta(stop_reason="tool_use", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=8),
        ),
        state,
    )
    events += process_anthropic_event(RawMessageStopEvent(type="message_stop"), state)

    call = next(e for e in events if isinstance(e, ToolCallStarted))
    assert call.call_id == "toolu_1"
    assert call.tool_name == "get_current_time"
    assert call.arguments == {"tz": "utc"}


def test_completion_settings_defaults():
    settings = CompletionSettings(model="gpt-x")
    assert settings.temperature == 0.7
    assert settings.max_tokens is None
    assert settings.extra == {}


# --- empty assistant content is not sendable -------------------------------
#
# Review finding: an assistant message with content="" and no tool calls
# went on the wire as an empty content block, which Anthropic rejects on the
# next turn ("all messages must have non-empty content"). Such a message is
# a real possibility — a provider round that produced only tool calls which
# were then cancelled, or a model that returned nothing.


def test_to_anthropic_params_drops_an_empty_assistant_message():
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=""),
        Message(role="user", content="still there?"),
    ]

    _system, out = to_anthropic_params(messages)

    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "still there?"},
    ]


def test_to_anthropic_params_keeps_an_empty_assistant_message_that_has_tool_calls():
    """Empty *text* with tool calls is the normal shape of a tool-only turn
    and must still be sent — the tool_use blocks are the content."""
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="get_time", arguments={})],
        ),
        Message(role="tool", content="42", tool_call_id="c1", name="get_time"),
    ]

    _system, out = to_anthropic_params(messages)

    assert out[0]["role"] == "assistant"
    assert [block["type"] for block in out[0]["content"]] == ["tool_use"]


# ---------------------------------------------------------------------------
# vision input (B1)
# ---------------------------------------------------------------------------
#
# Both providers' translation functions accept a keyword-only
# ``supports_vision`` flag (default False, so existing callers/tests above
# are unaffected). ``read_image_b64`` does real file I/O, so these tests
# write a tiny real PNG to disk rather than mocking it out.


def _write_tiny_png(tmp_path) -> str:
    # Smallest possible valid PNG (1x1, from the well-known base64 fixture).
    import base64

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    path = tmp_path / "tiny.png"
    path.write_bytes(png_bytes)
    return str(path)


def test_to_anthropic_params_attaches_image_blocks_for_tool_result_when_vision_enabled(tmp_path):
    from aida.providers.anthropic_ import to_anthropic_params

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(
            role="tool",
            content="[image artifact ...]",
            tool_call_id="toolu_1",
            images=[ImageRef(path=png_path, mime_type="image/png")],
        )
    ]

    _system, out = to_anthropic_params(messages, supports_vision=True)

    tool_result = out[0]["content"][0]
    assert tool_result["type"] == "tool_result"
    blocks = tool_result["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[-1] == {"type": "text", "text": "[image artifact ...]"}


def test_to_anthropic_params_omits_image_blocks_when_vision_disabled(tmp_path):
    from aida.providers.anthropic_ import to_anthropic_params

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(
            role="tool",
            content="[image artifact ...]",
            tool_call_id="toolu_1",
            images=[ImageRef(path=png_path, mime_type="image/png")],
        )
    ]

    _system, out = to_anthropic_params(messages, supports_vision=False)

    # Falls back to the plain text-only shape from before B1 existed.
    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "[image artifact ...]"}
            ],
        }
    ]


def test_to_anthropic_params_attaches_image_blocks_for_user_message(tmp_path):
    from aida.providers.anthropic_ import to_anthropic_params

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(role="user", content="what is this?", images=[ImageRef(path=png_path, mime_type="image/png")])
    ]

    _system, out = to_anthropic_params(messages, supports_vision=True)

    assert out[0]["role"] == "user"
    content = out[0]["content"]
    assert content[0]["type"] == "image"
    assert content[-1] == {"type": "text", "text": "what is this?"}


def test_to_anthropic_params_only_attaches_pixels_for_the_most_recent_images(tmp_path):
    from aida.providers.anthropic_ import to_anthropic_params
    from aida.providers.vision import MAX_ATTACHED_IMAGES

    png_path = _write_tiny_png(tmp_path)
    # One more image-bearing message than the cap allows.
    messages = [
        Message(role="user", content=f"image {i}", images=[ImageRef(path=png_path, mime_type="image/png")])
        for i in range(MAX_ATTACHED_IMAGES + 1)
    ]

    _system, out = to_anthropic_params(messages, supports_vision=True)

    # The oldest image-bearing message falls outside the cap and is sent as
    # plain text only; every later one still gets its pixels attached.
    assert out[0]["content"] == "image 0"
    for msg in out[1:]:
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "image"


def test_to_anthropic_params_skips_an_unreadable_image_path():
    from aida.providers.anthropic_ import to_anthropic_params

    messages = [
        Message(
            role="user",
            content="what is this?",
            images=[ImageRef(path="/no/such/file.png", mime_type="image/png")],
        )
    ]

    _system, out = to_anthropic_params(messages, supports_vision=True)

    # The unreadable image is silently dropped rather than raising or
    # sending a broken block; the text still goes through.
    assert out[0] == {"role": "user", "content": "what is this?"}


def test_to_openai_messages_attaches_image_url_parts_for_user_message_when_vision_enabled(tmp_path):
    from aida.providers.openai_compat import to_openai_messages

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(role="user", content="what is this?", images=[ImageRef(path=png_path, mime_type="image/png")])
    ]

    out = to_openai_messages(messages, supports_vision=True)

    content = out[0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[-1] == {"type": "text", "text": "what is this?"}


def test_to_openai_messages_never_attaches_images_to_tool_messages(tmp_path):
    """OpenAI chat-completions rejects multi-part content on tool messages —
    a hard API constraint, not a policy choice — so even with vision
    enabled and images present, a tool-result message stays plain text."""
    from aida.providers.openai_compat import to_openai_messages

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(
            role="tool",
            content="[image artifact ...]",
            tool_call_id="call_1",
            images=[ImageRef(path=png_path, mime_type="image/png")],
        )
    ]

    out = to_openai_messages(messages, supports_vision=True)

    assert out == [{"role": "tool", "tool_call_id": "call_1", "content": "[image artifact ...]"}]


def test_to_openai_messages_omits_image_parts_when_vision_disabled(tmp_path):
    from aida.providers.openai_compat import to_openai_messages

    png_path = _write_tiny_png(tmp_path)
    messages = [
        Message(role="user", content="what is this?", images=[ImageRef(path=png_path, mime_type="image/png")])
    ]

    out = to_openai_messages(messages, supports_vision=False)

    assert out == [{"role": "user", "content": "what is this?"}]


# ---------------------------------------------------------------------------
# providers.vision helpers
# ---------------------------------------------------------------------------


def test_select_images_within_cap_takes_the_most_recent_images():
    from aida.providers.vision import select_images_within_cap

    messages = [
        Message(role="user", content="a", images=[ImageRef(path="a.png")]),
        Message(role="user", content="b"),
        Message(role="user", content="c", images=[ImageRef(path="c.png")]),
        Message(role="user", content="d", images=[ImageRef(path="d.png")]),
    ]

    assert select_images_within_cap(messages, max_images=1) == {3: [ImageRef(path="d.png")]}
    assert select_images_within_cap(messages, max_images=2) == {
        3: [ImageRef(path="d.png")],
        2: [ImageRef(path="c.png")],
    }
    assert select_images_within_cap(messages, max_images=0) == {}


def test_select_images_within_cap_counts_images_not_messages():
    """The regression this cap exists to prevent. One message carrying many
    images — an MCP tool result returning a dozen plots, or a user dropping
    a folder of figures in — used to pass the cap entirely, because the cap
    selected the most recent four *messages* and the translators then
    attached every image in each."""
    from aida.providers.vision import select_images_within_cap

    messages = [
        Message(role="user", content="many", images=[ImageRef(path=f"{i}.png") for i in range(10)]),
    ]

    selected = select_images_within_cap(messages, max_images=4)

    assert sum(len(v) for v in selected.values()) == 4
    # The most recent four, and contiguous — not an arbitrary subset.
    assert selected[0] == [ImageRef(path=f"{i}.png") for i in (6, 7, 8, 9)]


def test_select_images_within_cap_splits_a_partially_included_message():
    from aida.providers.vision import select_images_within_cap

    messages = [
        Message(role="user", content="old", images=[ImageRef(path="a.png"), ImageRef(path="b.png")]),
        Message(role="user", content="new", images=[ImageRef(path="c.png")]),
    ]

    selected = select_images_within_cap(messages, max_images=2)

    assert selected == {1: [ImageRef(path="c.png")], 0: [ImageRef(path="b.png")]}


def test_read_image_b64_returns_none_for_a_path_that_does_not_exist():
    from aida.providers.vision import read_image_b64

    assert read_image_b64(ImageRef(path="/no/such/file.png")) is None


def test_read_image_b64_encodes_a_real_image(tmp_path):
    from aida.providers.vision import read_image_b64

    png_path = _write_tiny_png(tmp_path)
    result = read_image_b64(ImageRef(path=png_path, mime_type="image/png"))

    assert result is not None
    mime_type, data = result
    assert mime_type in ("image/png", "image/jpeg")  # PIL may re-encode
    import base64 as _b64

    _b64.b64decode(data)  # must round-trip as valid base64


def test_read_image_b64_guesses_mime_type_when_not_given(tmp_path):
    from aida.providers.vision import read_image_b64

    png_path = _write_tiny_png(tmp_path)
    result = read_image_b64(ImageRef(path=png_path, mime_type=None))

    assert result is not None
    mime_type, _data = result
    assert mime_type in ("image/png", "image/jpeg")


# ---------------------------------------------------------------------------
# prompt caching (B3)
# ---------------------------------------------------------------------------


def test_to_cached_system_param_wraps_with_ephemeral_cache_control():
    from aida.providers.anthropic_ import to_cached_system_param

    out = to_cached_system_param("be helpful")

    assert out == [{"type": "text", "text": "be helpful", "cache_control": {"type": "ephemeral"}}]


def test_to_cached_system_param_passes_through_none_and_empty():
    from aida.providers.anthropic_ import to_cached_system_param

    assert to_cached_system_param(None) is None
    assert to_cached_system_param("") == ""


def test_to_cached_tools_param_marks_only_the_last_tool():
    from aida.providers.anthropic_ import to_cached_tools_param

    tools = [
        {"name": "tool_a", "description": "", "input_schema": {}},
        {"name": "tool_b", "description": "", "input_schema": {}},
    ]

    out = to_cached_tools_param(tools)

    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}
    # Original list is untouched (no accidental shared mutation).
    assert "cache_control" not in tools[1]


def test_to_cached_tools_param_handles_empty_list():
    from aida.providers.anthropic_ import to_cached_tools_param

    assert to_cached_tools_param([]) == []


def test_process_anthropic_event_captures_cache_token_usage():
    from anthropic.types import Message as AnthropicMessage
    from anthropic.types import RawMessageStartEvent, RawMessageStopEvent, Usage

    from aida.core.events import UsageInfo
    from aida.providers.anthropic_ import _StreamState, process_anthropic_event

    state = _StreamState(message_id="m3")
    msg = AnthropicMessage(
        id="msg_3",
        content=[],
        model="claude-x",
        role="assistant",
        stop_reason=None,
        type="message",
        usage=Usage(input_tokens=100, output_tokens=0, cache_creation_input_tokens=50, cache_read_input_tokens=200),
    )
    events = process_anthropic_event(RawMessageStartEvent(type="message_start", message=msg), state)
    events += process_anthropic_event(RawMessageStopEvent(type="message_stop"), state)

    usage = next(e for e in events if isinstance(e, UsageInfo))
    assert usage.cache_creation_input_tokens == 50
    assert usage.cache_read_input_tokens == 200


def test_completion_settings_supports_vision_defaults_false():
    settings = CompletionSettings(model="gpt-x")
    assert settings.supports_vision is False


def test_finalize_stream_terminates_a_turn_that_never_sent_a_finish_reason():
    """Some OpenAI-*compatible* servers (and any dropped connection) end a
    stream after the last content delta without ever sending a
    ``finish_reason``. Without finalization the agent loop never saw a
    TextFinished and appended an *empty* assistant message — the reply the
    user just watched stream in vanished and was persisted blank."""
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    from aida.core.events import MessageFinished, TextFinished
    from aida.providers.openai_compat import _StreamState, finalize_stream, process_openai_chunk

    state = _StreamState(message_id="m1")

    def chunk(**kw):
        return ChatCompletionChunk(id="1", created=0, model="x", object="chat.completion.chunk", **kw)

    process_openai_chunk(
        chunk(choices=[Choice(index=0, delta=ChoiceDelta(content="partial answer"), finish_reason=None)]),
        state,
    )

    events = finalize_stream(state)

    finished = next(e for e in events if isinstance(e, TextFinished))
    assert finished.text == "partial answer"
    assert any(isinstance(e, MessageFinished) for e in events)


def test_finalize_stream_recovers_a_tool_call_the_truncated_stream_had_announced():
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import (
        Choice,
        ChoiceDelta,
        ChoiceDeltaToolCall,
        ChoiceDeltaToolCallFunction,
    )

    from aida.core.events import ToolCallStarted
    from aida.providers.openai_compat import _StreamState, finalize_stream, process_openai_chunk

    state = _StreamState(message_id="m2")
    process_openai_chunk(
        ChatCompletionChunk(
            id="1",
            created=0,
            model="x",
            object="chat.completion.chunk",
            choices=[
                Choice(
                    index=0,
                    delta=ChoiceDelta(
                        tool_calls=[
                            ChoiceDeltaToolCall(
                                index=0,
                                id="call_1",
                                function=ChoiceDeltaToolCallFunction(
                                    name="list_directory", arguments='{"path": "."}'
                                ),
                            )
                        ]
                    ),
                    finish_reason=None,
                )
            ],
        ),
        state,
    )

    events = finalize_stream(state)

    call = next(e for e in events if isinstance(e, ToolCallStarted))
    assert call.tool_name == "list_directory"
    assert call.arguments == {"path": "."}


def test_finalize_stream_is_a_no_op_after_a_normal_finish_reason():
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    from aida.providers.openai_compat import _StreamState, finalize_stream, process_openai_chunk

    state = _StreamState(message_id="m3")
    process_openai_chunk(
        ChatCompletionChunk(
            id="1",
            created=0,
            model="x",
            object="chat.completion.chunk",
            choices=[Choice(index=0, delta=ChoiceDelta(content="done"), finish_reason="stop")],
        ),
        state,
    )

    assert finalize_stream(state) == []


def test_finalize_stream_emits_nothing_when_the_stream_carried_no_content_at_all():
    """An immediately-failed request already surfaces as an AgentError —
    finalization must not invent an empty assistant turn on top of it."""
    from aida.providers.openai_compat import _StreamState, finalize_stream

    assert finalize_stream(_StreamState(message_id="m4")) == []
