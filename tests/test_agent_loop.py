from __future__ import annotations

import pytest

from aida.artifacts.base import FileArtifact, ImageArtifact, TextArtifact
from aida.core.agent import (
    CANCELLED_TOOL_RESULT,
    LENGTH_NUDGE,
    MAX_STALL_NUDGES,
    STALL_NUDGE,
    STALL_NUDGE_CALL_IN_REASONING,
    AgentLoop,
    has_tool_call_markup,
    is_stall,
    looks_like_announced_step,
    unknown_tool_message,
)
from aida.core.events import (
    AgentError,
    FileArtifactCreated,
    ImageArtifactCreated,
    MessageFinished,
    SteeringMessageDelivered,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnNudged,
)
from aida.core.tools import NativeTool, ToolResult
from aida.providers.base import CompletionSettings, Message, ToolSchema
from aida.providers.mock import MockProvider, MockToolCall, MockTurn


def _settings() -> CompletionSettings:
    return CompletionSettings(model="mock-model")


async def _get_current_time(_args):
    return ToolResult(content={"utc_iso": "2026-08-18T00:00:00Z"})


TIME_TOOL = NativeTool(
    schema=ToolSchema(
        name="get_current_time", description="Get time", parameters={"type": "object"}
    ),
    func=_get_current_time,
)


@pytest.mark.asyncio
async def test_simple_text_reply_streams_in_order():
    provider = MockProvider([MockTurn(text="Hello there")])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    types = [type(e).__name__ for e in events]
    assert types == ["TextStarted", "TextDelta", "TextFinished", "MessageFinished"]
    assert isinstance(events[0], TextStarted)
    assert any(isinstance(e, TextDelta) for e in events)
    finished = next(e for e in events if isinstance(e, MessageFinished))
    assert finished.stop_reason == "stop"
    # Final assistant message appended to history with the full text.
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "Hello there"


@pytest.mark.asyncio
async def test_tool_round_trip_end_to_end():
    provider = MockProvider(
        [
            MockTurn(
                text="checking the time",
                tool_calls=[MockToolCall(name="get_current_time", id="call_1")],
            ),
            MockTurn(text="It is 2026-08-18T00:00:00Z."),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_current_time": TIME_TOOL})
    messages = [Message(role="user", content="what time is it?")]

    events = [e async for e in loop.run(messages)]

    call_started = next(e for e in events if isinstance(e, ToolCallStarted))
    assert call_started.tool_name == "get_current_time"
    call_finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert call_finished.call_id == "call_1"
    assert call_finished.is_error is False
    assert call_finished.result == {"utc_iso": "2026-08-18T00:00:00Z"}

    # Two provider turns happened.
    assert len(provider.calls) == 2
    # History has: user, assistant(tool_calls), tool(result), assistant(final).
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert messages[-1].content == "It is 2026-08-18T00:00:00Z."


@pytest.mark.asyncio
async def test_unknown_tool_reports_error_but_loop_continues():
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="does_not_exist", id="call_1")]),
            MockTurn(text="fallback answer"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={})
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.is_error is True
    assert "Unknown tool" in str(finished.result)
    assert messages[-1].content == "fallback answer"


@pytest.mark.asyncio
async def test_tool_raising_exception_becomes_error_result():
    async def _boom(_args):
        raise RuntimeError("kaboom")

    tool = NativeTool(
        schema=ToolSchema(name="boom", description="", parameters={"type": "object"}), func=_boom
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="boom", id="call_1")]),
            MockTurn(text="recovered"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"boom": tool})
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.is_error is True
    assert "kaboom" in str(finished.result)


@pytest.mark.asyncio
async def test_tool_raising_a_keyerror_reports_the_exception_type_too():
    """The real bug report: a KeyError's own str() is just the missing key
    (`'path'`), unreadable on its own — `AgentLoop` must format the
    exception's type in too, e.g. "KeyError: 'path'", not bare `str(exc)`."""

    async def _boom(_args):
        raise KeyError("path")

    tool = NativeTool(
        schema=ToolSchema(name="boom", description="", parameters={"type": "object"}), func=_boom
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="boom", id="call_1")]),
            MockTurn(text="recovered"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"boom": tool})
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.is_error is True
    assert finished.result == "KeyError: 'path'"


# --- B: a tool call whose arguments never finished parsing (almost always
# stop_reason=max_tokens cutting off the streamed JSON mid-argument) must not
# be run — see aida.providers.base.UNPARSED_ARGUMENTS_KEY. -------------------


@pytest.mark.asyncio
async def test_tool_call_with_unparsed_arguments_is_not_run_and_reports_why():
    from aida.providers.base import UNPARSED_ARGUMENTS_KEY

    called = False

    async def _write_file(_args):
        nonlocal called
        called = True
        return ToolResult(content="wrote it")

    tool = NativeTool(
        schema=ToolSchema(name="write_file", description="", parameters={"type": "object"}),
        func=_write_file,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(
                        name="write_file",
                        id="call_1",
                        arguments={UNPARSED_ARGUMENTS_KEY: '{"path": "report.md", "content": "..'},
                    )
                ]
            ),
            MockTurn(text="retrying with a shorter write"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"write_file": tool})
    messages = [Message(role="user", content="write a report")]

    events = [e async for e in loop.run(messages)]

    assert called is False, "a call with unparsed arguments must never reach the tool"
    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.is_error is True
    assert "cut off" in str(finished.result)
    assert "output token limit" in str(finished.result)
    # The turn must still recover — same "loop continues" contract as an
    # unknown-tool or a raised-exception error result.
    assert messages[-1].content == "retrying with a shorter write"


@pytest.mark.asyncio
async def test_iteration_cap_reached():
    # A script where the model always requests a tool call, forever.
    turns = [MockTurn(tool_calls=[MockToolCall(name="get_current_time")]) for _ in range(10)]
    provider = MockProvider(turns)
    loop = AgentLoop(provider, _settings(), tools={"get_current_time": TIME_TOOL}, max_iterations=3)
    messages = [Message(role="user", content="loop forever")]

    events = [e async for e in loop.run(messages)]

    error = next(e for e in events if isinstance(e, AgentError))
    assert error.layer == "core"
    assert "iteration cap" in error.message
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_usage_info_gets_a_real_duration_stamped_by_the_loop():
    """Bug report: "Add time stamps to each message, may be tok/sec if
    available and wallclock time." A provider's UsageInfo carries token
    counts only — AgentLoop._run_turns is the one place that measures the
    wallclock span of a provider round-trip, so tok/sec is available
    regardless of whether a given provider's own API reports timing."""
    provider = MockProvider([MockTurn(text="hi", input_tokens=10, output_tokens=5)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    usage = next(e for e in events if type(e).__name__ == "UsageInfo")
    assert usage.duration_seconds is not None
    assert usage.duration_seconds >= 0.0


@pytest.mark.asyncio
async def test_provider_error_propagates_and_stops_loop():
    provider = MockProvider([MockTurn(error="connection refused")])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    assert len(events) == 1
    assert isinstance(events[0], AgentError)
    assert events[0].layer == "provider"
    # No assistant message should have been appended on a terminal error.
    assert messages[-1].role == "user"


@pytest.mark.asyncio
async def test_cancel_before_run_yields_cancelled_error():
    provider = MockProvider([MockTurn(text="should not be reached")])
    loop = AgentLoop(provider, _settings())
    loop.cancel()
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    assert len(events) == 1
    assert isinstance(events[0], AgentError)
    assert events[0].message == "cancelled"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_cancel_between_tool_calls_stops_further_execution():
    executed: list[str] = []

    async def _tracking_tool(_args):
        executed.append("ran")
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_tracking_tool,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(name="track", id="call_1"),
                    MockToolCall(name="track", id="call_2"),
                ]
            )
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]

    events = []
    async for event in loop.run(messages):
        events.append(event)
        if isinstance(event, ToolCallFinished):
            loop.cancel()  # cancel right after the first tool call finishes

    assert len(executed) == 1  # second tool call never ran
    assert isinstance(events[-1], AgentError)
    assert events[-1].message == "cancelled"


@pytest.mark.asyncio
async def test_image_artifact_in_tool_result_emits_image_artifact_created():
    async def _get_plot(_args):
        art = ImageArtifact(data=b"pngbytes", mime_type="image/png", path="/tmp/plot.png")
        return ToolResult(content="[image artifact ...]", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_plot", description="", parameters={"type": "object"}),
        func=_get_plot,
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_plot", id="call_1")]),
            MockTurn(text="here is the plot"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_plot": tool})
    messages = [Message(role="user", content="plot it")]

    events = [e async for e in loop.run(messages)]

    created = next(e for e in events if isinstance(e, ImageArtifactCreated))
    assert created.call_id == "call_1"
    assert created.mime_type == "image/png"
    assert created.path == "/tmp/plot.png"
    # The event must never carry the raw bytes — that would defeat the
    # whole point of a typed event over a flattened string.
    assert not hasattr(created, "data")

    # B1 (vision input): the tool-result Message appended to the shared
    # history must carry an ImageRef pointing at the artifact's on-disk
    # path, so a vision-capable provider can attach the actual pixels —
    # the raw bytes themselves are never copied onto the Message.
    tool_message = next(m for m in messages if m.role == "tool" and m.tool_call_id == "call_1")
    assert len(tool_message.images) == 1
    assert tool_message.images[0].path == "/tmp/plot.png"
    assert tool_message.images[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_tool_result_with_no_image_artifacts_leaves_message_images_empty():
    async def _get_time(_args):
        return ToolResult(content={"utc_iso": "2026-08-18T00:00:00Z"})

    tool = NativeTool(
        schema=ToolSchema(name="get_current_time", description="", parameters={"type": "object"}),
        func=_get_time,
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
            MockTurn(text="it is time"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_current_time": tool})
    messages = [Message(role="user", content="what time is it")]

    [e async for e in loop.run(messages)]

    tool_message = next(m for m in messages if m.role == "tool" and m.tool_call_id == "call_1")
    assert tool_message.images == []


@pytest.mark.asyncio
async def test_file_artifact_in_tool_result_emits_file_artifact_created():
    async def _get_report(_args):
        art = FileArtifact(path="/tmp/report.csv", mime_type="text/csv", data=b"a,b\n1,2\n")
        return ToolResult(content="[file artifact ...]", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_report", description="", parameters={"type": "object"}),
        func=_get_report,
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_report", id="call_1")]),
            MockTurn(text="here is the report"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_report": tool})
    messages = [Message(role="user", content="get report")]

    events = [e async for e in loop.run(messages)]

    created = next(e for e in events if isinstance(e, FileArtifactCreated))
    assert created.call_id == "call_1"
    assert created.path == "/tmp/report.csv"
    assert created.mime_type == "text/csv"


@pytest.mark.asyncio
async def test_file_artifact_without_path_emits_no_event():
    # A FileArtifact that was never saved to disk (no path) has nothing a
    # frontend could open — no event should be emitted for it, only for the
    # text content already carried in ToolCallFinished.
    async def _get_link(_args):
        art = FileArtifact(path=None, mime_type="text/csv", filename="remote.csv")
        return ToolResult(content="[file artifact ...]", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_link", description="", parameters={"type": "object"}),
        func=_get_link,
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_link", id="call_1")]),
            MockTurn(text="here is the link"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_link": tool})
    messages = [Message(role="user", content="get link")]

    events = [e async for e in loop.run(messages)]

    assert not any(isinstance(e, FileArtifactCreated) for e in events)


@pytest.mark.asyncio
async def test_text_artifact_in_result_emits_no_artifact_event():
    # TextArtifact carries no binary payload the frontend needs a separate
    # event for — it's already fully represented in ToolCallFinished.result.
    async def _get_text(_args):
        art = TextArtifact(text="hello")
        return ToolResult(content="hello", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_text", description="", parameters={"type": "object"}),
        func=_get_text,
    )
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_text", id="call_1")]),
            MockTurn(text="done"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_text": tool})
    messages = [Message(role="user", content="get text")]

    events = [e async for e in loop.run(messages)]

    assert not any(isinstance(e, (ImageArtifactCreated, FileArtifactCreated)) for e in events)


@pytest.mark.asyncio
async def test_mock_provider_exhausted_script_yields_error():
    provider = MockProvider([])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    assert isinstance(events[0], AgentError)
    assert "exhausted" in events[0].message


@pytest.mark.asyncio
async def test_mock_provider_text_is_chunked():
    provider = MockProvider([MockTurn(text="x" * 30, chunk_size=10)])

    deltas = []
    async for event in provider.complete([], [], _settings()):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
    assert deltas == ["x" * 10, "x" * 10, "x" * 10]


def test_text_finished_used_for_history_not_deltas():
    # Sanity: TextFinished exists distinctly from TextDelta in the event enum.
    assert TextFinished is not TextDelta


# --- debug logging (bug report: "may be add more console debug errors
# which we can disable later?") ---------------------------------------


@pytest.mark.asyncio
async def test_tool_dispatch_logs_call_and_result_at_debug(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="aida.agent")
    provider = MockProvider(
        [
            MockTurn(
                text="checking the time",
                tool_calls=[MockToolCall(name="get_current_time", id="call_1")],
            ),
            MockTurn(text="it's time"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"get_current_time": TIME_TOOL})
    messages = [Message(role="user", content="what time is it?")]

    _ = [e async for e in loop.run(messages)]

    messages_logged = [r.message for r in caplog.records if r.name == "aida.agent"]
    assert any("tool call: get_current_time" in m for m in messages_logged)
    assert any("finished ok" in m for m in messages_logged)


@pytest.mark.asyncio
async def test_unknown_tool_call_logs_a_warning(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="aida.agent")
    provider = MockProvider(
        [
            MockTurn(
                text="calling ghost tool",
                tool_calls=[MockToolCall(name="does_not_exist", id="call_1")],
            ),
            MockTurn(text="done"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={})
    messages = [Message(role="user", content="hi")]

    _ = [e async for e in loop.run(messages)]

    messages_logged = [r.message for r in caplog.records if r.name == "aida.agent"]
    assert any("unknown tool" in m.lower() for m in messages_logged)


# --- cancellation must leave a *valid* history -----------------------------
#
# Review finding: pressing Stop mid-tool-call permanently broke the
# conversation. The assistant message announcing every tool call is appended
# before the per-call loop runs, so bailing out on _cancelled left the
# un-executed calls with no matching tool result — a history Anthropic
# rejects with a 400 ("each tool_use must have a corresponding tool_result")
# and OpenAI likewise. Because those messages are also persisted as they
# land, the same wedge survived into a resumed session, and any crash or
# force-quit mid-turn produced it with no Stop involved.


def _announced_call_ids(messages: list[Message]) -> list[str]:
    return [tc.id for m in messages if m.role == "assistant" for tc in m.tool_calls]


def _answered_call_ids(messages: list[Message]) -> list[str]:
    return [m.tool_call_id for m in messages if m.role == "tool"]


@pytest.mark.asyncio
async def test_cancel_mid_tool_call_answers_every_announced_call():
    async def _slow_tool(_args):
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_slow_tool,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(name="track", id="call_1"),
                    MockToolCall(name="track", id="call_2"),
                    MockToolCall(name="track", id="call_3"),
                ]
            )
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]

    cancelled_once = False
    async for event in loop.run(messages):
        if isinstance(event, ToolCallFinished) and not cancelled_once:
            loop.cancel()
            cancelled_once = True

    assert _announced_call_ids(messages) == ["call_1", "call_2", "call_3"]
    assert _answered_call_ids(messages) == ["call_1", "call_2", "call_3"]
    cancelled = [m for m in messages if m.content == CANCELLED_TOOL_RESULT]
    assert [m.tool_call_id for m in cancelled] == ["call_2", "call_3"]


@pytest.mark.asyncio
async def test_cancel_mid_tool_call_emits_a_result_event_for_each_cancelled_call():
    """The GUI's tool rows are driven by ToolCallFinished; without one, a
    cancelled call's row would spin forever.

    Asserted as the *pairing* (every started call is finished) rather than
    a fixed list of ids: cancelling here happens on a ToolCallStarted,
    which the provider emits mid-stream, and the loop now cuts the stream
    off at that point — so how many calls get announced at all depends on
    exactly when Stop lands. What must hold regardless is that the caller
    never sees a start without its finish.
    """

    async def _tool(_args):
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_tool,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(name="track", id="c1"),
                    MockToolCall(name="track", id="c2"),
                ]
            )
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]

    events = []
    async for event in loop.run(messages):
        events.append(event)
        if isinstance(event, ToolCallStarted):
            loop.cancel()

    started = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert started == ["c1"], "the stream is cut off at the cancel, before c2 is announced"
    assert [e.call_id for e in finished] == started
    assert all(e.is_error for e in finished)
    # And the history stays valid for the same reason — see
    # test_cancel_mid_tool_call_answers_every_announced_call.
    assert _announced_call_ids(messages) == _answered_call_ids(messages) == started


@pytest.mark.asyncio
async def test_history_after_cancelled_turn_still_translates_for_anthropic():
    """The real failure mode was on the *next* request, at translation
    time — assert the repaired history survives it."""
    from aida.providers.anthropic_ import to_anthropic_params

    async def _tool(_args):
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_tool,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(name="track", id="c1"),
                    MockToolCall(name="track", id="c2"),
                ]
            )
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]
    async for event in loop.run(messages):
        if isinstance(event, ToolCallStarted):
            loop.cancel()  # mid-turn: c1 runs, c2 never does

    assert any(m.role == "assistant" and m.tool_calls for m in messages)
    _system, wire = to_anthropic_params(messages)
    tool_use_ids = {
        block["id"]
        for message in wire
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_use"
    }
    tool_result_ids = {
        block["tool_use_id"]
        for message in wire
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    }
    assert tool_use_ids  # the cancelled turn really did announce calls
    assert tool_use_ids == tool_result_ids


# --- mid-turn steering ----------------------------------------------------


@pytest.mark.asyncio
async def test_queued_message_is_delivered_at_the_next_round_trip():
    """User request: "when agent is working, user has no chance for input
    to the process... in Claude app user can type content in and it gets
    included in message in next turn, so I can tell agent what I forgot."
    """
    provider = MockProvider(
        [MockTurn(tool_calls=[MockToolCall(name="get_current_time")]), MockTurn(text="done")]
    )
    loop = AgentLoop(provider, _settings(), {"get_current_time": TIME_TOOL})
    messages = [Message(role="user", content="what time is it")]

    events = []
    async for event in loop.run(messages):
        events.append(event)
        if isinstance(event, ToolCallFinished):
            # Typed while the tool was running — the realistic moment.
            loop.queue_user_message("  also tell me the date  ")

    delivered = [e for e in events if isinstance(e, SteeringMessageDelivered)]
    assert [e.text for e in delivered] == ["also tell me the date"]  # stripped

    # Landed in the conversation as an ordinary user message, after the tool
    # result it could not have influenced and before the next request.
    roles = [(m.role, m.content) for m in messages]
    assert ("user", "also tell me the date") in roles
    assert roles.index(("user", "also tell me the date")) > next(
        i for i, m in enumerate(messages) if m.role == "tool"
    )


@pytest.mark.asyncio
async def test_queued_message_is_not_delivered_mid_tool_call():
    """Delivery has to wait for the round-trip boundary: appending a user
    message between a tool_use and its tool_result would leave a
    conversation both providers reject."""
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(name="get_current_time", id="c1"),
                    MockToolCall(name="get_current_time", id="c2"),
                ]
            ),
            MockTurn(text="done"),
        ]
    )
    loop = AgentLoop(provider, _settings(), {"get_current_time": TIME_TOOL})
    messages = [Message(role="user", content="hi")]

    seen_first_result = False
    order: list[str] = []
    async for event in loop.run(messages):
        if isinstance(event, ToolCallFinished) and not seen_first_result:
            seen_first_result = True
            loop.queue_user_message("interjection")
        if isinstance(event, (ToolCallFinished, SteeringMessageDelivered)):
            order.append(type(event).__name__)

    # Both tool results, then the interjection — never interleaved.
    assert order == ["ToolCallFinished", "ToolCallFinished", "SteeringMessageDelivered"]


@pytest.mark.asyncio
async def test_undelivered_queued_message_is_handed_back_not_eaten():
    """A turn that ends before reaching the queue must not swallow what the
    user typed — the GUI puts it back in the input box."""
    provider = MockProvider([MockTurn(text="done")])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    async for _event in loop.run(messages):
        pass
    loop.queue_user_message("too late")

    assert loop.take_undelivered_messages() == ["too late"]
    assert loop.take_undelivered_messages() == []  # drained, not duplicated


@pytest.mark.asyncio
async def test_blank_queued_message_is_ignored():
    loop = AgentLoop(MockProvider([MockTurn(text="ok")]), _settings())
    loop.queue_user_message("   \n  ")
    assert loop.take_undelivered_messages() == []


# --- before_round_trip hook (planning/completed/improvement_plan_2026-09.md §2: mid-
# turn context check — _trim_context used to run only once, before the turn
# started, with nothing re-checking history size across a long tool-calling
# turn) -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_round_trip_hook_runs_once_per_round_trip():
    calls = []

    async def hook():
        calls.append(True)
        return None

    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
            MockTurn(tool_calls=[MockToolCall(name="get_current_time", id="call_2")]),
            MockTurn(text="done"),
        ]
    )
    loop = AgentLoop(
        provider, _settings(), tools={"get_current_time": TIME_TOOL}, before_round_trip=hook
    )
    messages = [Message(role="user", content="hi")]

    [e async for e in loop.run(messages)]

    assert len(calls) == 3  # once before each of the three provider calls


@pytest.mark.asyncio
async def test_before_round_trip_hook_event_is_yielded():
    from aida.core.events import ContextTrimmed

    async def hook():
        return ContextTrimmed(dropped_turns=2, estimated_tokens=1234)

    provider = MockProvider([MockTurn(text="done")])
    loop = AgentLoop(provider, _settings(), before_round_trip=hook)
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    trimmed = [e for e in events if isinstance(e, ContextTrimmed)]
    assert len(trimmed) == 1
    assert trimmed[0].dropped_turns == 2
    assert trimmed[0].estimated_tokens == 1234


@pytest.mark.asyncio
async def test_before_round_trip_hook_returning_none_yields_nothing_extra():
    async def hook():
        return None

    provider = MockProvider([MockTurn(text="done")])
    loop = AgentLoop(provider, _settings(), before_round_trip=hook)
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    types = [type(e).__name__ for e in events]
    assert types == ["TextStarted", "TextDelta", "TextFinished", "MessageFinished"]


@pytest.mark.asyncio
async def test_no_hook_given_is_the_same_as_before():
    """Default (no before_round_trip) must behave exactly as it did before
    this hook existed — nothing to await, nothing extra yielded."""
    provider = MockProvider([MockTurn(text="done")])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    types = [type(e).__name__ for e in events]
    assert types == ["TextStarted", "TextDelta", "TextFinished", "MessageFinished"]


# --- Stop must not wait for the model to finish talking ---------------------
#
# User report (2026-10): "The Stop button seems to get stuck — I assume it is
# waiting for the provider to come back and then to stop. I have been staring
# at Working for 180 seconds and it is still going." Cancellation was checked
# only *between* round trips, so Stop pressed while a verbose model was
# part-way through an answer did nothing until the whole answer had streamed
# in. It is now checked between provider events as well.


@pytest.mark.asyncio
async def test_cancel_mid_stream_stops_consuming_the_provider_immediately():
    # One delta per character, so "how far did it get" is countable.
    provider = MockProvider([MockTurn(text="abcdefghij", chunk_size=1)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    deltas: list[str] = []
    async for event in loop.run(messages):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
            if len(deltas) == 3:
                loop.cancel()

    assert deltas == ["a", "b", "c"], "the remaining seven deltas must never arrive"


@pytest.mark.asyncio
async def test_cancel_mid_stream_yields_a_cancelled_error():
    provider = MockProvider([MockTurn(text="abcdefghij", chunk_size=1)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = []
    async for event in loop.run(messages):
        events.append(event)
        if isinstance(event, TextDelta):
            loop.cancel()

    assert isinstance(events[-1], AgentError)
    assert events[-1].message == "cancelled"


@pytest.mark.asyncio
async def test_cancel_mid_stream_keeps_the_text_the_user_already_saw():
    """The panel rendered those deltas as they arrived; dropping them from
    history would leave the transcript showing text the saved conversation
    does not have, and the model blind to its own half-answer."""
    provider = MockProvider([MockTurn(text="abcdefghij", chunk_size=1)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    seen = 0
    async for event in loop.run(messages):
        if isinstance(event, TextDelta):
            seen += 1
            if seen == 4:
                loop.cancel()

    assistant = [m for m in messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "abcd"
    assert assistant[0].tool_calls == []


@pytest.mark.asyncio
async def test_cancel_before_any_text_appends_no_empty_assistant_message():
    """Both providers reject an assistant message whose content is an empty
    string, so a stream cut off before its first token must leave history
    untouched rather than wedge the *next* request — the same class of bug
    the cancelled-tool-result path exists to prevent."""
    provider = MockProvider([MockTurn(text="abcdefghij", chunk_size=1)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    async for event in loop.run(messages):
        if isinstance(event, TextStarted):
            loop.cancel()  # before a single delta has landed

    assert [m.role for m in messages] == ["user"]


@pytest.mark.asyncio
async def test_cancel_mid_stream_closes_the_provider_generator():
    """Breaking out of an `async for` leaves the generator suspended and the
    provider's HTTP response open until the GC finalizes it — which is the
    difference between a Stop that stops and one that only looks stopped.
    AgentLoop wraps the stream in contextlib.aclosing; this proves the
    GeneratorExit actually reaches the provider."""
    closed: list[bool] = []

    class _ClosingProvider(MockProvider):
        async def complete(self, messages, tools, settings):
            try:
                yield TextStarted(message_id="m1")
                for index in range(1000):
                    yield TextDelta(message_id="m1", text=str(index))
                yield TextFinished(message_id="m1", text="never reached")
            except GeneratorExit:
                closed.append(True)
                raise

    loop = AgentLoop(_ClosingProvider(), _settings())
    messages = [Message(role="user", content="hi")]

    async for event in loop.run(messages):
        if isinstance(event, TextDelta):
            loop.cancel()

    assert closed == [True]


@pytest.mark.asyncio
async def test_cancel_mid_stream_does_not_run_an_announced_tool():
    """A call announced before the cancel landed still has to be answered
    (history validity), but must never actually execute."""
    ran: list[str] = []

    async def _tool(_args):
        ran.append("x")
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_tool,
    )
    provider = MockProvider(
        [MockTurn(tool_calls=[MockToolCall(name="track", id="c1")], chunk_size=1)]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]

    async for event in loop.run(messages):
        if isinstance(event, ToolCallStarted):
            loop.cancel()

    assert ran == []
    assert _announced_call_ids(messages) == _answered_call_ids(messages) == ["c1"]
    assert [m.content for m in messages if m.role == "tool"] == [CANCELLED_TOOL_RESULT]


@pytest.mark.asyncio
async def test_uncancelled_turn_still_streams_to_completion():
    """The cancel check sits inside the streaming loop, so the guard against
    it firing spuriously belongs right next to it."""
    provider = MockProvider([MockTurn(text="abcdefghij", chunk_size=1)])
    loop = AgentLoop(provider, _settings())
    messages = [Message(role="user", content="hi")]

    events = [event async for event in loop.run(messages)]

    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "abcdefghij"
    assert [m.content for m in messages if m.role == "assistant"] == ["abcdefghij"]
    assert not any(isinstance(e, AgentError) for e in events)


@pytest.mark.asyncio
async def test_native_tool_arguments_are_coerced_against_the_schema():
    """A weaker model sending ``"false"`` for a boolean used to reach a
    native tool unchanged, where ``bool("false")`` is True — e.g. an
    ``overwrite`` the model meant to leave off (review B9)."""
    received = {}

    async def _write(args):
        received.update(args)
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(
            name="write_file",
            description="",
            parameters={
                "type": "object",
                "properties": {"overwrite": {"type": "boolean"}, "count": {"type": "integer"}},
            },
        ),
        func=_write,
    )
    provider = MockProvider(
        [
            MockTurn(
                tool_calls=[
                    MockToolCall(
                        name="write_file",
                        arguments={"overwrite": "false", "count": "3"},
                        id="call_1",
                    )
                ]
            ),
            MockTurn(text="done"),
        ]
    )
    loop = AgentLoop(provider, _settings(), tools={"write_file": tool})
    [e async for e in loop.run([Message(role="user", content="hi")])]
    assert received == {"overwrite": False, "count": 3}


# --- replies that end a long task too early (TODO §6, 2026-11-21) -----------


def _tool_round(call_id: str = "t1") -> MockTurn:
    return MockTurn(tool_calls=[MockToolCall(name="get_current_time", id=call_id)])


async def _run(script: list[MockTurn], **loop_kwargs) -> tuple[list, list[Message], MockProvider]:
    provider = MockProvider(script)
    loop = AgentLoop(provider, _settings(), {"get_current_time": TIME_TOOL}, **loop_kwargs)
    messages = [Message(role="user", content="write the dossier")]
    events = [e async for e in loop.run(messages)]
    return events, messages, provider


@pytest.mark.asyncio
async def test_reply_cut_off_without_a_tool_call_gets_one_continue():
    events, messages, provider = await _run(
        [
            _tool_round(),
            MockTurn(text="Writing the dossier", stop_reason="length"),
            MockTurn(text="Done."),
        ]
    )
    nudges = [e for e in events if isinstance(e, TurnNudged)]
    assert [n.reason for n in nudges] == ["length"]
    assert len(provider.calls) == 3
    assert messages[-2].role == "user" and messages[-2].content == LENGTH_NUDGE
    assert messages[-3].content == "Writing the dossier"  # partial text kept
    assert messages[-1].content == "Done."
    assert not any(isinstance(e, AgentError) for e in events)


@pytest.mark.asyncio
async def test_a_second_cut_off_ends_the_turn_with_a_notice():
    events, messages, provider = await _run(
        [
            MockTurn(text="a", stop_reason="length"),
            MockTurn(text="b", stop_reason="length"),
            MockTurn(text="never reached"),
        ]
    )
    assert len(provider.calls) == 2
    assert sum(isinstance(e, TurnNudged) for e in events) == 1
    errors = [e for e in events if isinstance(e, AgentError)]
    assert len(errors) == 1 and "output token limit" in errors[0].message
    assert messages[-1].role == "assistant" and messages[-1].content == "b"


@pytest.mark.asyncio
async def test_an_empty_cut_off_reply_is_not_kept_as_an_empty_message():
    _, messages, _ = await _run([MockTurn(text="", stop_reason="length"), MockTurn(text="ok")])
    assert [m.role for m in messages] == ["user", "user", "assistant"]


@pytest.mark.asyncio
async def test_cut_off_mid_tool_arguments_is_answered_then_stops_the_second_time():
    from aida.providers.base import UNPARSED_ARGUMENTS_KEY

    partial = MockToolCall(name="get_current_time", arguments={UNPARSED_ARGUMENTS_KEY: '{"p'})
    events, messages, provider = await _run(
        [
            MockTurn(tool_calls=[partial], stop_reason="length"),
            MockTurn(tool_calls=[partial], stop_reason="length"),
            MockTurn(text="never reached"),
        ]
    )
    assert len(provider.calls) == 2
    # Both partial calls were answered, so the history stays well-formed.
    tool_results = [m for m in messages if m.role == "tool"]
    assert len(tool_results) == 2
    assert all("cut off" in m.content for m in tool_results)
    errors = [e for e in events if isinstance(e, AgentError)]
    assert len(errors) == 1 and "output token limit" in errors[0].message


@pytest.mark.asyncio
async def test_short_announcement_after_tool_work_gets_a_stall_nudge():
    events, messages, provider = await _run(
        [
            _tool_round(),
            MockTurn(text="I have everything needed. Writing the dossier now."),
            MockTurn(text="The dossier is saved to dossier.md and covers all 12 proposals."),
        ]
    )
    nudges = [e for e in events if isinstance(e, TurnNudged)]
    assert [n.reason for n in nudges] == ["stall"]
    assert len(provider.calls) == 3
    assert messages[-2].content == STALL_NUDGE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "Done — the plot is saved to fig.png.",  # a normal short ending
        "Should I also write the summary table?",  # a question to the user
        "Let me know if you need anything else.",  # a closing offer
        "Writing it now. " + "Details of the result. " * 20,  # a long answer
    ],
)
async def test_no_stall_nudge_on_a_normal_end_of_task(reply):
    events, _, provider = await _run([_tool_round(), MockTurn(text=reply)])
    assert not any(isinstance(e, TurnNudged) for e in events)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_no_stall_nudge_without_tool_work_or_when_disabled():
    events, _, _ = await _run([MockTurn(text="Let me think about that.")])
    assert not any(isinstance(e, TurnNudged) for e in events)
    events, _, provider = await _run(
        [_tool_round(), MockTurn(text="Let me check one thing.")], auto_continue_on_stall=False
    )
    assert not any(isinstance(e, TurnNudged) for e in events)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_stall_nudges_are_capped_per_turn():
    script = []
    for i in range(MAX_STALL_NUDGES + 1):
        script += [_tool_round(f"t{i}"), MockTurn(text="Let me check one more thing.")]
    events, _, _ = await _run(script)
    stalls = [e for e in events if isinstance(e, TurnNudged) and e.reason == "stall"]
    assert len(stalls) == MAX_STALL_NUDGES


def test_looks_like_announced_step_examples():
    assert looks_like_announced_step("Let me check one thing — the deadline.")
    assert looks_like_announced_step("Next, I compare the two budgets:")
    assert not looks_like_announced_step("All 12 proposals are reviewed.")
    assert not looks_like_announced_step("")


@pytest.mark.asyncio
async def test_round_trip_end_is_logged_with_stop_reason_and_reasoning(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="aida")
    await _run([MockTurn(text="hi", output_tokens=7, stop_reason="stop")])
    line = next(r.getMessage() for r in caplog.records if "round trip" in r.getMessage())
    assert "stop_reason=stop" in line and "output_tokens=7" in line and "reasoning=0" in line


# --- second test run (2026-11-21 evening) ------------------------------------


@pytest.mark.parametrize(
    ("text_chars", "reasoning_chars", "expected"),
    [(175, 17_316, True), (139, 15_094, True), (2_452, 1_526, False), (6_500, 60, False)],
)
def test_stall_shape_matches_the_measured_run(text_chars, reasoning_chars, expected):
    """The rows of the 2026-11-21 table: the two real stalls nudge, the
    normal endings do not."""
    text = "…so the calibration file will fix them rather than repeat them" + "." * text_chars
    assert is_stall(text[:text_chars], reasoning_chars) is expected


def test_stall_shape_never_overrides_a_question_or_a_closing_offer():
    assert not is_stall("Should I write the calibration file now?", 20_000)
    assert not is_stall("Let me know if you want changes.", 20_000)


async def _run_with_reasoning(text: str, reasoning: str) -> tuple[list, list[Message]]:
    """A tool round, then a short reply carrying ``reasoning``."""

    class _ReasoningProvider(MockProvider):
        async def complete(self, messages, tools, settings):
            async for event in super().complete(messages, tools, settings):
                if isinstance(event, MessageFinished) and len(self.calls) == 2:
                    event = MessageFinished(
                        message_id=event.message_id,
                        stop_reason=event.stop_reason,
                        reasoning_chars=len(reasoning),
                        reasoning_tail=reasoning[-1500:],
                    )
                yield event

    provider = _ReasoningProvider([_tool_round(), MockTurn(text=text), MockTurn(text="Done.")])
    loop = AgentLoop(provider, _settings(), {"get_current_time": TIME_TOOL})
    messages = [Message(role="user", content="score the proposals")]
    events = [e async for e in loop.run(messages)]
    return events, messages


@pytest.mark.asyncio
async def test_short_reply_behind_long_reasoning_is_nudged(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="aida")
    events, messages = await _run_with_reasoning(
        "…so the calibration file will fix them rather than repeat them.", "plan " * 4000
    )
    assert [e.reason for e in events if isinstance(e, TurnNudged)] == ["stall"]
    assert messages[-2].content == STALL_NUDGE
    assert any("end of reasoning" in r.getMessage() for r in caplog.records)
    assert not any("plan plan" in m.content for m in messages)  # reasoning stays out


@pytest.mark.asyncio
async def test_a_tool_call_written_in_the_reasoning_gets_the_send_it_nudge():
    reasoning = 'Now write it. <tool_call>{"name": "write_file", "arguments": {"path": "a.md"}}'
    events, messages = await _run_with_reasoning("Writing it.", reasoning)
    assert [e.reason for e in events if isinstance(e, TurnNudged)] == ["stall"]
    assert messages[-2].content == STALL_NUDGE_CALL_IN_REASONING


def test_tool_call_markup_forms_are_recognised():
    assert has_tool_call_markup("x <function=write_file> y")
    assert has_tool_call_markup('{"name": "edit_file", "arguments": {}}')
    assert not has_tool_call_markup("I will call write_file next.")


def test_unknown_tool_suggests_the_intended_one_with_its_arguments():
    edit = NativeTool(
        schema=ToolSchema(
            name="edit_file",
            description="",
            parameters={"type": "object", "required": ["path", "old_text", "new_text"]},
        ),
        func=_get_current_time,
    )
    tools = {"edit_file": edit, "get_current_time": TIME_TOOL}
    message = unknown_tool_message("edit_file=path", tools)
    assert "Did you mean 'edit_file'?" in message
    assert "path, old_text, new_text" in message
    assert "Did you mean 'get_current_time'" in unknown_tool_message("get_curent_time", tools)
    assert unknown_tool_message("totally_new", tools) == "Unknown tool: totally_new"


@pytest.mark.asyncio
async def test_a_garbled_tool_name_is_not_run_but_explained():
    events, messages, _ = await _run(
        [MockTurn(tool_calls=[MockToolCall(name="get_current_time=now", id="x")]), MockTurn("ok")]
    )
    result = next(m for m in messages if m.role == "tool")
    assert "Did you mean 'get_current_time'?" in result.content
