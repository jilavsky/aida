"""Provider-agnostic types and the ``LLMProvider`` interface.

Every concrete provider (``openai_compat.py``, ``anthropic_.py``, and the
test-only ``MockProvider`` below) speaks this one internal message/tool
format and translates to/from its own SDK's dialect internally — callers
(the agent loop, the CLI, eventually the GUI) never see SDK-specific types.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from aida.core.events import AgentEvent

#: Sampling parameters a provider is allowed to *drop and retry* when an
#: endpoint rejects them (see ``unsupported_request_param``). Deliberately
#: short: only knobs whose absence changes sampling, never anything whose
#: absence would change what the model is asked to do (``tools``,
#: ``system``, ``messages``, ``max_tokens``).
DROPPABLE_REQUEST_PARAMS = ("temperature", "top_p", "top_k")

#: Phrases a 400 uses to say "I know this parameter, but not for this
#: model". Newer models reject ``temperature`` outright ("`temperature` is
#: deprecated for this model"); OpenAI's reasoning models say
#: "Unsupported value"/"unsupported parameter"; various local servers say
#: "unknown"/"not allowed". Matching on the phrase *plus* the parameter
#: name (rather than the name alone) keeps an unrelated 400 that merely
#: mentions temperature in prose from silently changing the request.
_REJECTION_PHRASES = (
    "deprecated",
    "unsupported",
    "not supported",
    "does not support",
    "no longer supported",
    "unrecognized",
    "unknown parameter",
    "unknown argument",
    "not allowed",
    "not permitted",
    "cannot be specified",
    "must not be specified",
    "unexpected keyword",
    "extra fields not permitted",
)


#: HTTP statuses that can never mean "this request parameter is wrong":
#: auth/permission/not-found/timeout/rate-limit, plus anything 5xx. Every
#: other status is eligible, deliberately including 200 — a proxy in front
#: of a model (the ANL Argo endpoint) answers 200 with an error envelope
#: quoting the upstream 400, which the SDK surfaces as an APIStatusError
#: whose status_code is that 200.
_NON_PARAM_STATUSES = frozenset({401, 403, 404, 408, 429})


def is_param_rejection_status(status_code: int) -> bool:
    """Whether a status code could plausibly carry a "bad parameter"
    complaint — see ``_NON_PARAM_STATUSES``."""
    return status_code not in _NON_PARAM_STATUSES and status_code < 500


def unsupported_request_param(error_text: str, params: Iterable[str]) -> str | None:
    """Name of the request parameter an endpoint's 400 is complaining
    about, or ``None`` when the error is about something else.

    Providers use this to recover from the one failure mode that is purely
    a wire-format mismatch rather than a real problem with the request: a
    model that refuses a sampling knob AIDA sent by default. ``params`` is
    the set of keys actually in the outgoing request, so a parameter AIDA
    never sent is never "the problem", and only keys in
    ``DROPPABLE_REQUEST_PARAMS`` are ever returned — a 400 about
    ``messages`` or ``tools`` must surface to the user, not get silently
    retried with the field removed.
    """
    text = error_text.lower()
    if not any(phrase in text for phrase in _REJECTION_PHRASES):
        return None
    candidates = [name for name in params if name in DROPPABLE_REQUEST_PARAMS]
    for name in DROPPABLE_REQUEST_PARAMS:
        if name in candidates and re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


@dataclass
class ToolCall:
    """A tool invocation the model requested."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageRef:
    """A pointer to an on-disk image to send as vision input alongside a
    ``Message`` (B1). ``path`` is the only thing that must be right —
    ``mime_type`` is guessed from the extension at translation time
    (``aida.providers.vision.read_image_b64``) when not given. Deliberately
    a path, not bytes: every ``ImageArtifact`` this gets built from is
    already on disk by the time the agent loop sees it (``McpManager``
    saves before returning a tool result), so there is nothing to gain from
    loading the bytes any earlier than the one place that actually needs
    them — the provider translation step, right before the request goes
    out."""

    path: str
    mime_type: str | None = None


@dataclass
class Message:
    """One turn in the conversation, in AIDA's internal format.

    ``role`` is one of ``"system"``, ``"user"``, ``"assistant"``, ``"tool"``.
    An assistant message that requested tool calls carries them in
    ``tool_calls``; a tool-result message sets ``tool_call_id`` (and
    optionally ``name``) to say which call it answers.

    ``images`` (B1) is additive: ``content`` stays plain ``str`` everywhere
    (nothing that only reads ``.content`` breaks), and this list is empty
    for every message that isn't a tool result carrying an ``ImageArtifact``
    or a GUI image attachment. What actually gets sent as vision input for
    a given provider call — whether images are sent at all
    (``CompletionSettings.supports_vision``), how many of the most recent
    ones, downscaled how far — is a translation-time decision
    (``aida.providers.vision``), not something baked into the message at
    append time; the full list here is the true, uncapped record.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    images: list[ImageRef] = field(default_factory=list)


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
    one provider request. ``temperature``/``max_tokens`` are normally
    filled in from the active ``ProviderProfile`` (B2) rather than left at
    these defaults; the defaults here just preserve prior behavior for a
    profile that doesn't set them.

    ``supports_vision`` (B1) gates whether a translation function
    (``to_anthropic_params``/``to_openai_messages``) attaches any image
    content blocks at all — default ``False`` because not every endpoint
    AIDA talks to understands them (a small text-only local model can
    error on an unexpected image content block), so it's opt-in per
    profile rather than assumed from the provider kind.
    """

    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    supports_vision: bool = False
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
