from __future__ import annotations

import pytest

from aida.artifacts.base import FileArtifact, ImageArtifact, TextArtifact
from aida.core.agent import CANCELLED_TOOL_RESULT, AgentLoop
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
from aida.core.tools import NativeTool, ToolResult
from aida.providers.base import CompletionSettings, Message, ToolSchema
from aida.providers.mock import MockProvider, MockToolCall, MockTurn


def _settings() -> CompletionSettings:
    return CompletionSettings(model="mock-model")


async def _get_current_time(_args):
    return ToolResult(content={"utc_iso": "2026-08-18T00:00:00Z"})


TIME_TOOL = NativeTool(
    schema=ToolSchema(name="get_current_time", description="Get time", parameters={"type": "object"}),
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
            MockTurn(text="checking the time", tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
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
            MockTurn(text="checking the time", tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
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
            MockTurn(text="calling ghost tool", tool_calls=[MockToolCall(name="does_not_exist", id="call_1")]),
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
    cancelled call's row would spin forever."""

    async def _tool(_args):
        return ToolResult(content="ok")

    tool = NativeTool(
        schema=ToolSchema(name="track", description="", parameters={"type": "object"}),
        func=_tool,
    )
    provider = MockProvider(
        [MockTurn(tool_calls=[MockToolCall(name="track", id="c1"), MockToolCall(name="track", id="c2")])]
    )
    loop = AgentLoop(provider, _settings(), tools={"track": tool})
    messages = [Message(role="user", content="hi")]

    events = []
    async for event in loop.run(messages):
        events.append(event)
        if isinstance(event, ToolCallStarted):
            loop.cancel()

    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert [e.call_id for e in finished] == ["c1", "c2"]
    assert all(e.is_error for e in finished)


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
        [MockTurn(tool_calls=[MockToolCall(name="track", id="c1"), MockToolCall(name="track", id="c2")])]
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
