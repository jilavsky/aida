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

DEFAULT_MAX_TOKENS = 4096

#: Anthropic's cache_control marker for a "cache this, reuse for ~5 min"
#: breakpoint (B3). Ephemeral is the only kind AIDA needs — there is no
#: cross-session cache to keep warm on purpose, just the same system
#: prompt + tool schema list resent unchanged on every turn of one running
#: session.
_EPHEMERAL_CACHE_CONTROL = {"type": "ephemeral"}

# Anthropic stop_reason -> AIDA's normalized MessageFinished.stop_reason.
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


def _anthropic_image_blocks(images: list[Any]) -> list[dict[str, Any]]:
    """``ImageRef`` list -> Anthropic ``image`` content blocks, skipping any
    that can no longer be read (see ``read_image_b64``). Anthropic's own
    guidance is to place image blocks before the text that refers to them,
    so callers prepend this to the text block rather than appending it."""
    blocks: list[dict[str, Any]] = []
    for ref in images:
        encoded = read_image_b64(ref)
        if encoded is None:
            continue
        mime_type, data = encoded
        blocks.append(
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": data}}
        )
    return blocks


def to_anthropic_params(
    messages: list[Message], *, supports_vision: bool = False
) -> tuple[str | None, list[dict[str, Any]]]:
    """AIDA ``Message`` list -> (system prompt string, Anthropic ``messages``).

    Anthropic takes the system prompt as a separate top-level parameter, not
    as a message in the list — any ``role="system"`` messages are pulled out
    and joined.

    **Parallel tool calls are coalesced.** One assistant turn may contain
    several ``tool_use`` blocks; AIDA's internal format then carries one
    ``role="tool"`` ``Message`` per result, and the Anthropic API requires
    every one of those results to come back in a *single* user message
    holding several ``tool_result`` blocks. Emitting one user message per
    result (which is what this did before) is not a hard API error — the
    API combines consecutive same-role messages — but it does train the
    model to stop issuing parallel tool calls at all, which matters here
    precisely because pyIrena MCP work is full of "plot all of these"
    fan-outs. So consecutive ``role="tool"`` messages are merged into one
    user message, in order.

    **Vision (B1).** When ``supports_vision`` is true, the most recent
    ``aida.providers.vision.MAX_ATTACHED_IMAGES`` image-bearing messages
    (tool results carrying an ``ImageArtifact``, or a GUI image attachment)
    get their actual pixels attached as ``image`` content blocks — a
    ``tool_result`` can hold them directly; a plain ``user`` message gets a
    multi-part ``content`` list instead of a bare string. Every other
    image-bearing message (older than the cap, or when ``supports_vision``
    is false) still carries its text-policy description exactly as before
    — only the pixels are capped/gated, never the fact that an image
    exists at all.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []
    # index -> the specific images to attach for that message; the cap
    # counts images, not messages, so one result carrying many of them
    # contributes only its most recent few. See select_images_within_cap.
    selected_images = select_images_within_cap(messages) if supports_vision else {}

    def flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for idx, m in enumerate(messages):
        if m.role != "tool":
            flush_tool_results()

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
            # An empty tool_result content block is rejected by the API; a
            # tool that legitimately returned nothing still needs to say so.
            text = m.content or "(no output)"
            image_blocks = _anthropic_image_blocks(selected_images.get(idx, []))
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": [*image_blocks, {"type": "text", "text": text}]
                    if image_blocks
                    else text,
                }
            )
        elif m.content or selected_images.get(idx):
            image_blocks = _anthropic_image_blocks(selected_images.get(idx, []))
            if image_blocks:
                out.append(
                    {
                        "role": m.role,
                        "content": [*image_blocks, {"type": "text", "text": m.content}]
                        if m.content
                        else image_blocks,
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        # else: a message with no content and no tool calls is dropped.
        # The API rejects an empty content block outright ("all messages
        # must have non-empty content"), and an assistant turn *can*
        # legitimately end up empty — a provider round that produced only
        # tool calls which were then cancelled, or a model that returned
        # nothing at all. Sending it poisons every later turn in the
        # conversation; dropping it loses nothing, since there was no
        # content to convey.

    flush_tool_results()
    system = "\n\n---\n\n".join(system_parts) if system_parts else None
    return system, out


def to_cached_system_param(system: str | None) -> str | list[dict[str, Any]] | None:
    """Wrap the system prompt as a single cached text block (B3).

    Every turn of a session resends the same workspace context + skills +
    MCP server instructions unchanged — Anthropic's prompt cache is exactly
    for that. A ``cache_control: {"type": "ephemeral"}`` marker on this one
    block tells the API "cache everything up to here"; the system prompt is
    always the first thing in the request, so one marker covers all of it.
    ``None``/empty pass through unchanged — nothing to cache."""
    if not system:
        return system
    return [{"type": "text", "text": system, "cache_control": dict(_EPHEMERAL_CACHE_CONTROL)}]


def to_cached_tools_param(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the *last* tool definition as a cache breakpoint (B3).

    Anthropic caches everything up to and including a ``cache_control``
    marker, so one marker on the last tool in the list covers the entire
    tool schema array — often 100+ namespaced MCP tools resent unchanged on
    every turn. Returns ``tools`` unchanged if empty."""
    if not tools:
        return tools
    tools = list(tools)
    tools[-1] = {**tools[-1], "cache_control": dict(_EPHEMERAL_CACHE_CONTROL)}
    return tools


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
    #: B3: cache stats, reported alongside input/output tokens so the
    #: savings prompt caching is meant to produce are actually visible
    #: rather than an invisible backend detail — see UsageInfo.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

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
            # Cache stats are only ever reported here (message_start), not
            # on the later message_delta — that one only ever carries
            # output_tokens.
            state.cache_creation_input_tokens = (
                getattr(usage, "cache_creation_input_tokens", None) or 0
            )
            state.cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", None) or 0

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
                UsageInfo(
                    input_tokens=state.input_tokens,
                    output_tokens=state.output_tokens,
                    cache_creation_input_tokens=state.cache_creation_input_tokens,
                    cache_read_input_tokens=state.cache_read_input_tokens,
                )
            )

    return events


class AnthropicProvider(LLMProvider):
    """Claude direct or via a custom ``base_url`` (e.g. the ANL Argo proxy)."""

    layer_name = "provider"

    def __init__(
        self, *, model: str, base_url: str | None = None, api_key: str | None = None
    ) -> None:
        self.model = model
        self._client = AsyncAnthropic(base_url=base_url, api_key=api_key or "not-needed")
        # model -> sampling params this endpoint has already rejected once
        # (see _param_to_drop). Remembered per provider instance so the
        # doomed request is made at most once per model per session, not
        # once per turn.
        self._dropped_params: dict[str, set[str]] = {}

    def _param_to_drop(self, exc: Exception, kwargs: dict[str, Any]) -> str | None:
        """Name of the sampling parameter this endpoint just refused, or
        ``None`` when the error is about anything else.

        Newer models reject ``temperature`` outright ("`temperature` is
        deprecated for this model"), OpenAI-family models behind the same
        proxy reject a *value* ("Only the default (1) value is supported"),
        and a proxy fronting several models can accept it for one and
        refuse it for the next — so a profile cannot know statically
        whether to send it.

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
        message_id = f"anthropic-{id(messages)}-{len(messages)}"
        system, anthropic_messages = to_anthropic_params(
            messages, supports_vision=settings.supports_vision
        )

        kwargs: dict[str, Any] = {
            "model": settings.model or self.model,
            "messages": anthropic_messages,
            "max_tokens": settings.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": True,
            **settings.extra,
        }
        if settings.temperature is not None:
            kwargs["temperature"] = settings.temperature
        if system is not None:
            # B3: cached as a single ephemeral block — see
            # to_cached_system_param's docstring.
            kwargs["system"] = to_cached_system_param(system)
        if tools:
            kwargs["tools"] = to_cached_tools_param(to_anthropic_tools(tools))

        self._without_known_bad_params(kwargs)

        # Retry loop, not a plain try: a request the endpoint refuses only
        # because of a sampling parameter is retried once per offending
        # parameter with that parameter removed (see _param_to_drop).
        # ``emitted`` is the guard that keeps this honest — a failure after
        # the first event of the turn reached the caller can't be replayed
        # without duplicating text, so it is reported as-is.
        while True:
            state = _StreamState(message_id=message_id)
            emitted = False
            error: AgentError | None = None
            retry_param: str | None = None
            try:
                stream = await self._client.messages.create(**kwargs)
                async for event in stream:
                    for out_event in process_anthropic_event(event, state):
                        emitted = True
                        yield out_event
            except AuthenticationError as exc:
                error = AgentError(
                    layer=self.layer_name, message="authentication failed", detail=str(exc)
                )
            except NotFoundError as exc:
                error = AgentError(
                    layer=self.layer_name, message="model not found", detail=str(exc)
                )
            except APIConnectionError as exc:
                error = AgentError(
                    layer=self.layer_name, message="connection failed", detail=str(exc)
                )
            except APIStatusError as exc:
                retry_param = None if emitted else self._param_to_drop(exc, kwargs)
                error = AgentError(
                    layer=self.layer_name, message=f"API error ({exc.status_code})", detail=str(exc)
                )
            except AnthropicError as exc:
                retry_param = None if emitted else self._param_to_drop(exc, kwargs)
                error = AgentError(
                    layer=self.layer_name, message="unexpected provider error", detail=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 - see LLMProvider.complete's contract
                # LLMProvider.complete promises callers "yields a single
                # AgentError ... rather than raising" — AgentLoop relies on
                # that, treating an AgentError as the turn's terminator. Only
                # AnthropicError subclasses were caught here, so anything else
                # the SDK or the network stack raised (a TypeError from a
                # malformed kwargs override, an httpx-level error not wrapped
                # by the SDK, a JSON decode failure mid-stream) escaped the
                # async generator and broke that contract at the call site.
                # OpenAICompatProvider already ends with the same blanket
                # catch; this makes the two behave alike.
                retry_param = None if emitted else self._param_to_drop(exc, kwargs)
                error = AgentError(
                    layer=self.layer_name,
                    message="unexpected error",
                    detail=f"{type(exc).__name__}: {exc}",
                )

            if retry_param is not None:
                kwargs.pop(retry_param, None)
                continue
            if error is not None:
                yield error
            return

    async def ping(self) -> bool:
        """Reachability + auth check for ``aida doctor`` and the Providers…
        dialog's "Test" button — PLAN.md §1.5: this used to send a real,
        billed 1-token ``messages.create`` call just to check the endpoint
        is up, on every doctor run and every click. ``models.list`` is a
        free, authenticated metadata endpoint on the real Anthropic API —
        same reachability/auth signal (a bad key still 401s), zero cost.

        Falls back to the old paid ping on ``NotFoundError`` specifically:
        a custom ``base_url`` proxy (the ANL Argo proxy this provider is
        built to also speak to — see the module docstring) may implement
        only the Messages API, not the newer Models API, and a working
        proxy must not be reported unreachable just because it lacks an
        endpoint AIDA never actually needs. Any other failure (auth,
        connection, ...) is a real negative — no point spending a token on
        a second call that would fail the same way."""
        try:
            await self._client.models.list(limit=1)
            return True
        except NotFoundError:
            pass  # proxy doesn't implement /v1/models — fall back below
        except Exception:  # noqa: BLE001 - ping must never raise
            return False
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
    "to_cached_system_param",
    "to_cached_tools_param",
]
