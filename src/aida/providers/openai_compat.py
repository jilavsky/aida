"""OpenAI-compatible provider (Ollama, LM Studio, Unsloth Desktop, llama.cpp
server, OpenAI itself, ...) via the ``openai`` SDK with a custom ``base_url``.

The translation between AIDA's internal ``Message``/``ToolSchema`` format and
the OpenAI chat-completions wire format is deliberately split into pure,
network-free functions (``to_openai_messages``, ``to_openai_tools``,
``process_openai_chunk``) so it can be unit-tested with synthetic chunk
objects — see ``tests/test_provider_translation.py`` — without mocking HTTP.
``OpenAICompatProvider.complete()`` itself is the thin, mostly-untested-by-unit-tests
glue that drives the real streaming call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
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
from aida.providers.vision import images_within_cap, read_image_b64

# Provider-normalized stop reasons (aida.core.events.MessageFinished.stop_reason).
_FINISH_REASON_MAP = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "length": "length",
    "content_filter": "stop",
}


def _openai_image_parts(images: list[Any]) -> list[dict[str, Any]]:
    """``ImageRef`` list -> OpenAI ``image_url`` content parts (data URLs),
    skipping any that can no longer be read (see ``read_image_b64``)."""
    parts: list[dict[str, Any]] = []
    for ref in images:
        encoded = read_image_b64(ref)
        if encoded is None:
            continue
        mime_type, data = encoded
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}})
    return parts


def to_openai_messages(messages: list[Message], *, supports_vision: bool = False) -> list[dict[str, Any]]:
    """AIDA ``Message`` list -> OpenAI chat-completions ``messages`` list.

    **Vision (B1).** When ``supports_vision`` is true, the most recent
    ``aida.providers.vision.MAX_ATTACHED_IMAGES`` image-bearing *user*
    messages (GUI image attachments) get ``image_url`` data-URL parts
    alongside their text — the shape Ollama/LM Studio's vision models
    expect. Deliberately **not** extended to ``role="tool"`` messages: the
    OpenAI chat-completions wire format only allows multi-part content on
    user/assistant messages, so a tool-result image on this provider stays
    text-described only (its ``ImageArtifact`` was never attached to a
    ``tool`` message's ``images`` for this reason — see
    ``aida.core.agent``). Anthropic's ``to_anthropic_params`` has no such
    restriction and does attach both.
    """
    out: list[dict[str, Any]] = []
    image_indices = images_within_cap(messages) if supports_vision else set()
    for idx, m in enumerate(messages):
        if m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                }
            )
        else:
            image_parts = _openai_image_parts(m.images) if idx in image_indices and m.images else []
            if image_parts:
                content: Any = [*image_parts, {"type": "text", "text": m.content}] if m.content else image_parts
                out.append({"role": m.role, "content": content})
            else:
                out.append({"role": m.role, "content": m.content})
    return out


def to_openai_tools(tools: list[ToolSchema]) -> list[dict[str, Any]] | None:
    """AIDA ``ToolSchema`` list -> OpenAI ``tools`` param. None if empty (some
    OpenAI-compatible servers reject an explicit empty tools list)."""
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


@dataclass
class _StreamState:
    """Accumulator threaded through successive chunks of one streamed turn."""

    message_id: str
    text_parts: list[str] = field(default_factory=list)
    tool_call_builders: dict[int, dict[str, Any]] = field(default_factory=dict)
    started: bool = False

    def full_text(self) -> str:
        return "".join(self.text_parts)


def _ensure_started(state: _StreamState, events: list[AgentEvent]) -> None:
    if not state.started:
        events.append(TextStarted(message_id=state.message_id))
        state.started = True


def process_openai_chunk(chunk: Any, state: _StreamState) -> list[AgentEvent]:
    """Translate one ``ChatCompletionChunk`` into zero or more ``AgentEvent``.

    Pure function: takes/returns plain data, mutates only ``state``. This is
    what makes it unit-testable without a real (or mocked-HTTP) streaming
    connection — tests just construct a chunk-shaped object.
    """
    events: list[AgentEvent] = []

    choices = getattr(chunk, "choices", None) or []
    if not choices:
        usage = getattr(chunk, "usage", None)
        if usage:
            events.append(
                UsageInfo(
                    input_tokens=usage.prompt_tokens or 0,
                    output_tokens=usage.completion_tokens or 0,
                    total_tokens=usage.total_tokens,
                )
            )
        return events

    choice = choices[0]
    delta = choice.delta

    if delta and getattr(delta, "content", None):
        _ensure_started(state, events)
        state.text_parts.append(delta.content)
        events.append(TextDelta(message_id=state.message_id, text=delta.content))

    if delta and getattr(delta, "tool_calls", None):
        for tc_delta in delta.tool_calls:
            idx = tc_delta.index
            builder = state.tool_call_builders.setdefault(
                idx, {"id": None, "name": None, "arguments": ""}
            )
            if tc_delta.id:
                builder["id"] = tc_delta.id
            fn = getattr(tc_delta, "function", None)
            if fn:
                if fn.name:
                    builder["name"] = fn.name
                if fn.arguments:
                    builder["arguments"] += fn.arguments

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason:
        _ensure_started(state, events)
        events.append(TextFinished(message_id=state.message_id, text=state.full_text()))
        for idx, builder in state.tool_call_builders.items():
            try:
                arguments = json.loads(builder["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparsed_arguments": builder["arguments"]}
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
                stop_reason=_FINISH_REASON_MAP.get(finish_reason, "stop"),
            )
        )

    return events


class OpenAICompatProvider(LLMProvider):
    """Any OpenAI-compatible chat-completions endpoint."""

    layer_name = "provider"

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        # The SDK requires a non-empty api_key even for endpoints (Ollama,
        # LM Studio) that ignore it entirely.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        message_id = f"oai-{id(messages)}-{len(messages)}"
        state = _StreamState(message_id=message_id)
        kwargs: dict[str, Any] = {
            "model": settings.model or self.model,
            "messages": to_openai_messages(messages, supports_vision=settings.supports_vision),
            "temperature": settings.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            **settings.extra,
        }
        if settings.max_tokens is not None:
            kwargs["max_tokens"] = settings.max_tokens
        oai_tools = to_openai_tools(tools)
        if oai_tools is not None:
            kwargs["tools"] = oai_tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                for event in process_openai_chunk(chunk, state):
                    yield event
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
        except Exception as exc:  # noqa: BLE001 - any unexpected SDK error still surfaces as AgentError
            yield AgentError(layer=self.layer_name, message="unexpected provider error", detail=str(exc))

    async def ping(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception:  # noqa: BLE001 - ping must never raise
            return False

    async def aclose(self) -> None:
        await self._client.close()


__all__ = [
    "OpenAICompatProvider",
    "process_openai_chunk",
    "to_openai_messages",
    "to_openai_tools",
]
