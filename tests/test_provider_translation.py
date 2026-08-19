"""Translation-layer tests for both provider dialects (Phase 2 requirement:
"Provider dialect translation tests (schema in/out) for both SDKs").

These test the pure functions (``process_openai_chunk``,
``process_anthropic_event``, and the message/tool converters) directly
against real SDK-typed objects, rather than mocking HTTP transport — see the
module docstrings in ``openai_compat.py``/``anthropic_.py`` for why that
split makes this possible without a network layer at all.
"""

from __future__ import annotations

from aida.providers.base import CompletionSettings, Message, ToolCall, ToolSchema

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
