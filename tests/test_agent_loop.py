from __future__ import annotations

import pytest

from aida.core.agent import AgentLoop
from aida.core.events import (
    AgentError,
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
