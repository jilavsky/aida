"""Anthropic provider — Claude direct **and** Claude via the ANL Argo proxy
(``base_url=https://apps.inside.anl.gov/argoapi/``, ``api_key`` = ANL
username — the BeamlineAdvisor pattern), via the ``anthropic`` SDK.

Named ``anthropic_`` (trailing underscore) only to avoid shadowing the
``anthropic`` package itself on ``sys.path``.

Same split as ``openai_compat.py``: translation is pure/testable
(``to_anthropic_params``, ``to_anthropic_tools``, ``process_anthropic_event``);
``AnthropicProvider.complete()`` is the thin glue around the real streaming
call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    AuthenticationError,
    NotFoundError,
)

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
from aida.providers.base import CompletionSettings, LLMProvider, Message, ToolSchema

DEFAULT_MAX_TOKENS = 4096

# Anthropic stop_reason -> AIDA's normalized MessageFinished.stop_reason.
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


def to_anthropic_params(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """AIDA ``Message`` list -> (system prompt string, Anthropic ``messages``).

    Anthropic takes the system prompt as a separate top-level parameter, not
    as a message in the list — any ``role="system"`` messages are pulled out
    and joined.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
        elif m.role == "assistant" and m.tool_calls:
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": content})
        elif m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content,
                        }
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    system = "\n\n---\n\n".join(system_parts) if system_parts else None
    return system, out


def to_anthropic_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]


@dataclass
class _StreamState:
    message_id: str
    text_parts: list[str] = field(default_factory=list)
    tool_blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    started: bool = False
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def full_text(self) -> str:
        return "".join(self.text_parts)


def _ensure_started(state: _StreamState, events: list[AgentEvent]) -> None:
    if not state.started:
        events.append(TextStarted(message_id=state.message_id))
        state.started = True


def process_anthropic_event(event: Any, state: _StreamState) -> list[AgentEvent]:
    """Translate one raw Anthropic streaming event into zero or more
    ``AgentEvent``. Pure function — see ``process_openai_chunk`` for why."""
    events: list[AgentEvent] = []
    etype = getattr(event, "type", None)

    if etype == "message_start":
        _ensure_started(state, events)
        usage = getattr(event.message, "usage", None)
        if usage:
            state.input_tokens = usage.input_tokens or 0

    elif etype == "content_block_start":
        block = event.content_block
        if getattr(block, "type", None) == "tool_use":
            state.tool_blocks[event.index] = {
                "id": block.id,
                "name": block.name,
                "partial_json": "",
            }

    elif etype == "content_block_delta":
        delta = event.delta
        dtype = getattr(delta, "type", None)
        if dtype == "text_delta":
            _ensure_started(state, events)
            state.text_parts.append(delta.text)
            events.append(TextDelta(message_id=state.message_id, text=delta.text))
        elif dtype == "input_json_delta":
            builder = state.tool_blocks.setdefault(
                event.index, {"id": None, "name": None, "partial_json": ""}
            )
            builder["partial_json"] += delta.partial_json

    elif etype == "message_delta":
        delta = getattr(event, "delta", None)
        if delta is not None:
            state.stop_reason = getattr(delta, "stop_reason", None) or state.stop_reason
        usage = getattr(event, "usage", None)
        if usage:
            state.output_tokens = usage.output_tokens or 0

    elif etype == "message_stop":
        _ensure_started(state, events)
        events.append(TextFinished(message_id=state.message_id, text=state.full_text()))
        for idx, builder in sorted(state.tool_blocks.items()):
            try:
                arguments = json.loads(builder["partial_json"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparsed_arguments": builder["partial_json"]}
            events.append(
                ToolCallStarted(
                    call_id=builder["id"] or f"call-{idx}",
                    tool_name=builder["name"] or "",
                    arguments=arguments,
                )
            )
        events.append(
            MessageFinished(
                message_id=state.message_id,
                stop_reason=_STOP_REASON_MAP.get(state.stop_reason, "stop"),
            )
        )
        if state.input_tokens or state.output_tokens:
            events.append(
                UsageInfo(input_tokens=state.input_tokens, output_tokens=state.output_tokens)
            )

    return events


class AnthropicProvider(LLMProvider):
    """Claude direct or via a custom ``base_url`` (e.g. the ANL Argo proxy)."""

    layer_name = "provider"

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        self._client = AsyncAnthropic(base_url=base_url, api_key=api_key or "not-needed")

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        message_id = f"anthropic-{id(messages)}-{len(messages)}"
        state = _StreamState(message_id=message_id)
        system, anthropic_messages = to_anthropic_params(messages)

        kwargs: dict[str, Any] = {
            "model": settings.model or self.model,
            "messages": anthropic_messages,
            "max_tokens": settings.max_tokens or DEFAULT_MAX_TOKENS,
            "temperature": settings.temperature,
            "stream": True,
            **settings.extra,
        }
        if system is not None:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)

        try:
            stream = await self._client.messages.create(**kwargs)
            async for event in stream:
                for out_event in process_anthropic_event(event, state):
                    yield out_event
        except AuthenticationError as exc:
            yield AgentError(layer=self.layer_name, message="authentication failed", detail=str(exc))
        except NotFoundError as exc:
            yield AgentError(layer=self.layer_name, message="model not found", detail=str(exc))
        except APIConnectionError as exc:
            yield AgentError(layer=self.layer_name, message="connection failed", detail=str(exc))
        except APIStatusError as exc:
            yield AgentError(
                layer=self.layer_name, message=f"API error ({exc.status_code})", detail=str(exc)
            )
        except AnthropicError as exc:
            yield AgentError(layer=self.layer_name, message="unexpected provider error", detail=str(exc))

    async def ping(self) -> bool:
        try:
            await self._client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:  # noqa: BLE001 - ping must never raise
            return False

    async def aclose(self) -> None:
        await self._client.close()


__all__ = [
    "AnthropicProvider",
    "process_anthropic_event",
    "to_anthropic_params",
    "to_anthropic_tools",
]
