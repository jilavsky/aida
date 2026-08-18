"""Provider-agnostic types and the ``LLMProvider`` interface.

Every concrete provider (``openai_compat.py``, ``anthropic_.py``, and the
test-only ``MockProvider`` below) speaks this one internal message/tool
format and translates to/from its own SDK's dialect internally — callers
(the agent loop, the CLI, eventually the GUI) never see SDK-specific types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aida.core.events import AgentEvent


@dataclass
class ToolCall:
    """A tool invocation the model requested."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """One turn in the conversation, in AIDA's internal format.

    ``role`` is one of ``"system"``, ``"user"``, ``"assistant"``, ``"tool"``.
    An assistant message that requested tool calls carries them in
    ``tool_calls``; a tool-result message sets ``tool_call_id`` (and
    optionally ``name``) to say which call it answers.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSchema:
    """A tool definition offered to the model, in JSON-Schema-parameters form
    (the common denominator both OpenAI-compatible and Anthropic tool-calling
    dialects use)."""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )


@dataclass
class CompletionSettings:
    """Sampling / request settings for one ``complete()`` call.

    Per-conversation looping concerns (max iterations, etc.) live in
    ``aida.core.agent.AgentLoop``, not here — this is just what goes into
    one provider request.
    """

    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Common interface every provider implements.

    ``complete()`` is an async generator yielding ``AgentEvent``s (see
    ``aida.core.events`` for the exact sequence a well-behaved provider
    produces). A provider that hits a network/auth/model error yields a
    single ``AgentError`` (``layer="provider"``) and ends the stream rather
    than raising — callers should not need a try/except around iteration.
    """

    #: Set by subclasses; used to tag AgentError.layer and in diagnostics.
    layer_name: str = "provider"

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        """Stream one assistant turn for the given conversation."""
        raise NotImplementedError

    @abstractmethod
    async def ping(self) -> bool:
        """Best-effort, cheap reachability check (used by profile validation
        and ``aida doctor``). Must never raise — return False on failure."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any underlying connections (HTTP client, etc).

        Default no-op for providers with nothing to close (e.g. MockProvider).
        Real SDK-backed providers override this — without it, letting
        ``asyncio.run()`` close the event loop out from under an open
        ``httpx``-based client produces a noisy
        ``GeneratorExit``/``RuntimeError`` on interpreter shutdown. Callers
        (``ChatSession``, and anywhere else that owns a provider's lifetime)
        must call this when they're done with a provider — on session end
        and on profile switch alike.
        """
        return None
