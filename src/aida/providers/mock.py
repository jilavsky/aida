"""``MockProvider`` — a scripted, network-free ``LLMProvider`` for tests.

Lets the agent loop, CLI harness, and profile-switching logic be tested
deterministically (streaming order, tool round-trips, iteration caps, error
propagation) without a real model or network access, per PLAN.md §7's
``MockProvider`` requirement.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aida.core.events import (
    AgentError,
    AgentEvent,
    MessageFinished,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallStarted,
    UsageInfo,
)
from aida.providers.base import CompletionSettings, LLMProvider, Message, ToolCall, ToolSchema

DEFAULT_CHUNK_SIZE = 12


@dataclass
class MockToolCall:
    """A tool call the mock model should request on this turn."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class MockTurn:
    """One scripted response: what ``MockProvider.complete()`` yields on its
    Nth call. Exactly one of a plain-text answer or one/more tool calls is
    the normal case; ``error`` short-circuits to an ``AgentError``."""

    text: str = ""
    tool_calls: list[MockToolCall] = field(default_factory=list)
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    chunk_size: int = DEFAULT_CHUNK_SIZE


def _chunks(text: str, size: int) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


class MockProvider(LLMProvider):
    """Replays a fixed script of :class:`MockTurn`, one per ``complete()`` call.

    Calling ``complete()`` more times than the script has turns yields an
    ``AgentError`` rather than raising, matching real-provider error
    behavior and keeping test call sites simple.
    """

    layer_name = "provider"

    def __init__(self, script: list[MockTurn] | None = None) -> None:
        self._script: list[MockTurn] = list(script or [])
        self.calls: list[tuple[list[Message], list[ToolSchema], CompletionSettings]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append((list(messages), list(tools), settings))

        if not self._script:
            yield AgentError(layer=self.layer_name, message="MockProvider script exhausted")
            return

        turn = self._script.pop(0)
        message_id = f"mock-{len(self.calls)}"

        if turn.error:
            yield AgentError(layer=self.layer_name, message=turn.error)
            return

        yield TextStarted(message_id=message_id)
        for chunk in _chunks(turn.text, turn.chunk_size):
            yield TextDelta(message_id=message_id, text=chunk)
        yield TextFinished(message_id=message_id, text=turn.text)

        for mtc in turn.tool_calls:
            call_id = mtc.id or f"call-{uuid.uuid4().hex[:8]}"
            yield ToolCallStarted(call_id=call_id, tool_name=mtc.name, arguments=dict(mtc.arguments))

        stop_reason = "tool_calls" if turn.tool_calls else "stop"
        yield MessageFinished(message_id=message_id, stop_reason=stop_reason)

        if turn.input_tokens or turn.output_tokens:
            yield UsageInfo(input_tokens=turn.input_tokens, output_tokens=turn.output_tokens)

    async def ping(self) -> bool:
        return True


__all__ = ["MockProvider", "MockToolCall", "MockTurn", "ToolCall"]
