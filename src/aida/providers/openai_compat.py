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

from aida.config.logging_setup import get_logger
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
from aida.providers.base import (
    CompletionSettings,
    LLMProvider,
    Message,
    ToolSchema,
    is_param_rejection_status,
    unsupported_request_param,
)
from aida.providers.vision import read_image_b64, select_images_within_cap

_logger = get_logger("provider")

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
    # index -> the specific images to attach for that message; the cap
    # counts images, not messages, so one result carrying many of them
    # contributes only its most recent few. See select_images_within_cap.
    selected_images = select_images_within_cap(messages) if supports_vision else {}
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
            image_parts = _openai_image_parts(selected_images.get(idx, []))
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
    #: Set once a ``finish_reason`` chunk has produced the turn's
    #: TextFinished/ToolCallStarted/MessageFinished events, so
    #: ``finalize_stream`` can tell "the server ended the turn properly"
    #: from "the stream just stopped".
    finished: bool = False

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
        state.finished = True
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


def finalize_stream(state: _StreamState) -> list[AgentEvent]:
    """Events for a stream that ended without ever sending a
    ``finish_reason``, so the turn still terminates properly.

    ``process_openai_chunk`` only emits ``TextFinished``/``ToolCallStarted``/
    ``MessageFinished`` when a chunk carries a ``finish_reason``. OpenAI
    itself always sends one, but OpenAI-*compatible* servers are a much
    looser population — llama.cpp/LM Studio builds and proxies have been
    seen ending a stream after the last content delta, and any dropped
    connection does the same. AIDA then finished the turn having emitted
    only ``TextDelta``s: ``AgentLoop`` never saw a ``TextFinished``, so it
    appended an assistant message with **empty** content (and no tool
    calls) and returned — the user watched text stream into the window and
    then be replaced by nothing, with the reply also persisted empty, and
    any tool call the model had actually requested silently dropped.
    Reconstructing the turn from what did arrive is strictly better than
    discarding it. Returns ``[]`` when the stream ended normally."""
    if state.finished:
        return []
    if not state.started and not state.text_parts and not state.tool_call_builders:
        return []  # nothing arrived at all — an AgentError already covers it
    events: list[AgentEvent] = []
    _ensure_started(state, events)
    state.finished = True
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
    events.append(MessageFinished(message_id=state.message_id, stop_reason="stop"))
    return events


class OpenAICompatProvider(LLMProvider):
    """Any OpenAI-compatible chat-completions endpoint."""

    layer_name = "provider"

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        # The SDK requires a non-empty api_key even for endpoints (Ollama,
        # LM Studio) that ignore it entirely.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")
        # model -> sampling params this endpoint has already rejected once
        # (see _param_to_drop).
        self._dropped_params: dict[str, set[str]] = {}

    def _param_to_drop(self, exc: Exception, kwargs: dict[str, Any]) -> str | None:
        """Name of the sampling parameter this endpoint just refused, or
        ``None`` when the error is about anything else.

        See ``AnthropicProvider._param_to_drop`` for the full rationale. Same
        problem here, and this is where the user actually hit it: OpenAI-
        family models reject a temperature *value* ("Unsupported value:
        'temperature' does not support 0.7 with this model. Only the
        default (1) value is supported."), and small local servers reject
        assorted sampling knobs they never implemented.

        Deliberately typed on plain ``Exception`` and reading the status
        code with ``getattr``: the error does not always arrive as an
        ``APIStatusError``. A proxy that answers 200 and then puts the
        upstream 400 in the event stream (the ANL Argo pattern) makes the
        SDK raise its status-less base error instead, which is how "Only
        the default (1) value is supported" reached the user as an
        unrecoverable "unexpected provider error" — the text said exactly
        what to drop, but the retry was gated on the wrong exception class.
        So every error branch asks this, and the answer depends on what the
        message says, not on how the SDK chose to wrap it.

        Rather than making the user configure per-model quirks, a rejection
        naming a droppable sampling knob is taken as the endpoint telling
        us to leave it out: the caller drops that one key and retries, and
        it is remembered here so the doomed request is made at most once
        per model per session. An error about anything else — or about
        ``messages``/``tools``/``max_tokens`` — is never retried and
        reaches the user as an ``AgentError``.
        """
        if not is_param_rejection_status(getattr(exc, "status_code", None)):
            return None
        name = unsupported_request_param(str(exc), kwargs)
        if name is None:
            return None
        model = str(kwargs.get("model", ""))
        self._dropped_params.setdefault(model, set()).add(name)
        _logger.info(
            "model %s rejected %r; sending without it for the rest of this session", model, name
        )
        return name
    def _without_known_bad_params(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Strip params this endpoint already rejected for this model."""
        for name in self._dropped_params.get(str(kwargs.get("model", "")), ()):
            kwargs.pop(name, None)
        return kwargs

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        settings: CompletionSettings,
    ) -> AsyncIterator[AgentEvent]:
        message_id = f"oai-{id(messages)}-{len(messages)}"
        kwargs: dict[str, Any] = {
            "model": settings.model or self.model,
            "messages": to_openai_messages(messages, supports_vision=settings.supports_vision),
            "stream": True,
            "stream_options": {"include_usage": True},
            **settings.extra,
        }
        if settings.temperature is not None:
            kwargs["temperature"] = settings.temperature
        if settings.max_tokens is not None:
            kwargs["max_tokens"] = settings.max_tokens
        oai_tools = to_openai_tools(tools)
        if oai_tools is not None:
            kwargs["tools"] = oai_tools

        self._without_known_bad_params(kwargs)

        # Retry loop, not a plain try — see AnthropicProvider.complete:
        # a request refused only because of a sampling parameter is retried
        # once per offending parameter with that parameter removed, and
        # only while nothing of this turn has been yielded yet.
        while True:
            state = _StreamState(message_id=message_id)
            emitted = False
            error: AgentError | None = None
            retry_param: str | None = None
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    for event in process_openai_chunk(chunk, state):
                        emitted = True
                        yield event
                # A compatible server that ended the stream without a
                # finish_reason would otherwise leave the turn unterminated —
                # see finalize_stream.
                for event in finalize_stream(state):
                    yield event
            except AuthenticationError as exc:
                error = AgentError(layer=self.layer_name, message="authentication failed", detail=str(exc))
            except NotFoundError as exc:
                error = AgentError(layer=self.layer_name, message="model not found", detail=str(exc))
            except APIConnectionError as exc:
                error = AgentError(layer=self.layer_name, message="connection failed", detail=str(exc))
            except APIStatusError as exc:
                retry_param = None if emitted else self._param_to_drop(exc, kwargs)
                error = AgentError(
                    layer=self.layer_name, message=f"API error ({exc.status_code})", detail=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 - any unexpected SDK error still surfaces as AgentError
                # Not just belt-and-braces: an error delivered *inside* an
                # SSE stream (HTTP 200, then an error event — the Argo
                # proxy's shape) reaches us as the SDK's status-less base
                # APIError, which lands right here rather than in the
                # APIStatusError branch above.
                retry_param = None if emitted else self._param_to_drop(exc, kwargs)
                error = AgentError(
                    layer=self.layer_name, message="unexpected provider error", detail=str(exc)
                )

            if retry_param is not None:
                kwargs.pop(retry_param, None)
                continue
            if error is not None:
                yield error
            return

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
    "finalize_stream",
    "process_openai_chunk",
    "to_openai_messages",
    "to_openai_tools",
]
