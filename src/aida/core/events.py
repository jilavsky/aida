"""The core<->frontend event model (PLAN.md §3, hard rule 4).

Every provider, the agent loop, and (later) the MCP manager communicate with
whatever frontend is listening — CLI today, Qt in Phase 5, conceivably a web
frontend later — exclusively through this stream of plain, JSON-serializable
dataclasses. No frontend-specific type (a Qt signal, an HTML fragment, ...)
may appear here; that is what keeps `aida.core` and friends Qt-free.

An "agent turn" emits, in order:

    TextStarted
    TextDelta*              (zero or more streamed text chunks)
    TextFinished
    ToolCallStarted*         (zero or more — the *model* deciding to call a tool;
                              the provider never executes tools itself)
    MessageFinished
    UsageInfo?               (omitted if the provider doesn't report usage)

If the caller's tools produced results, the *agent loop* (not the provider)
emits `ToolCallFinished` / `ImageArtifactCreated` / `FileArtifactCreated` for
each executed call before looping back for another turn.

Between two round trips the agent loop may also emit `SteeringMessageDelivered`
— text the user typed while the turn was running, now appended to the
conversation.

`AgentError` can appear at any point and always ends the stream for that
`complete()`/`run()` call — it is not just informational. The one exception
is a *transient* provider failure (``AgentError.retryable``) before any of
the turn was shown: ``aida.providers.retry.complete_with_retries`` turns
that into a `ProviderRetrying` notice and sends the request again.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


def _base_dict(event: Any) -> dict[str, Any]:
    data = dataclasses.asdict(event)
    data["type"] = type(event).__name__
    return data


@dataclass(frozen=True)
class TextStarted:
    """A new assistant text turn has begun streaming."""

    message_id: str

    to_dict = _base_dict


@dataclass(frozen=True)
class TextDelta:
    """An incremental chunk of assistant text."""

    message_id: str
    text: str

    to_dict = _base_dict


@dataclass(frozen=True)
class TextFinished:
    """The assistant text turn is complete; ``text`` is the full accumulated text."""

    message_id: str
    text: str

    to_dict = _base_dict


@dataclass(frozen=True)
class ToolCallStarted:
    """The model has decided to call a tool. Emitted by the *provider* — it
    does not mean the tool has run yet."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]

    to_dict = _base_dict


@dataclass(frozen=True)
class ToolCallFinished:
    """A tool call has actually been executed. Emitted by the *agent loop*
    (aida.core.agent), never by a provider."""

    call_id: str
    tool_name: str
    result: Any
    is_error: bool = False

    to_dict = _base_dict


@dataclass(frozen=True)
class ImageArtifactCreated:
    """A tool result produced an image (PLAN.md §3 hard rule 3: typed, never
    a raw base64 string flattened into text)."""

    artifact_id: str
    call_id: str
    mime_type: str
    path: str | None = None

    to_dict = _base_dict


@dataclass(frozen=True)
class FileArtifactCreated:
    """A tool result produced a non-image file artifact."""

    artifact_id: str
    call_id: str
    path: str
    mime_type: str | None = None

    to_dict = _base_dict


#: How much of the end of a reply's reasoning a provider keeps.
REASONING_TAIL_CHARS = 1500


@dataclass(frozen=True)
class MessageFinished:
    """One provider turn (one call to ``LLMProvider.complete``) has ended.

    ``stop_reason`` is provider-normalized: ``"stop"`` (final answer),
    ``"tool_calls"`` (model wants tool results before continuing),
    ``"length"`` (hit max_tokens), or ``"cancelled"``.

    ``reasoning_chars`` is the length of any reasoning ("thinking") text the
    provider streamed separately from the reply; it is logged, never shown
    or stored. Reasoning counts against the output limit, so it is what
    explains a reply cut off at ``"length"`` with little visible text.
    """

    message_id: str
    stop_reason: str
    reasoning_chars: int = 0
    #: The last ``REASONING_TAIL_CHARS`` of that reasoning, for the stall
    #: diagnostics in ``aida.core.agent``; logged there, never stored.
    reasoning_tail: str = ""

    to_dict = _base_dict


@dataclass(frozen=True)
class UsageInfo:
    """Token accounting for one provider turn, when the provider reports it.

    ``duration_seconds`` (bug report: "may be tok/sec if available and
    wallclock time") is filled in by ``aida.core.agent.AgentLoop`` itself
    — the wallclock span of the ``provider.complete()`` round-trip this
    usage belongs to — rather than by any individual provider, so it's
    available uniformly regardless of whether a given provider's API
    reports timing at all.

    ``cache_creation_input_tokens``/``cache_read_input_tokens`` (B3,
    Anthropic only — always 0 for a provider that doesn't do prompt
    caching): what prompt caching actually cost and saved this turn.
    ``cache_read_input_tokens`` is the number that matters for "the
    savings are visible" — tokens served from cache instead of being
    billed at full input price; ``cache_creation_input_tokens`` is the
    (one-time, slightly pricier) cost of writing the cache in the first
    place, normally only nonzero on a session's first turn or right after
    the system prompt/tools actually change.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int | None = None
    duration_seconds: float | None = None
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    to_dict = _base_dict


@dataclass(frozen=True)
class RetrievalPerformed:
    """Emitted by ``aida.cli.chat.ChatSession.send()`` when a workspace's
    knowledge base(s) (Phase 8) retrieved passages for this turn's
    question, right before they're injected into the model's context —
    "event carries retrieval info so the GUI can show 'used these
    passages'" (planning/completed/phase08_rag.md). Never emitted for a workspace
    with no knowledge bases configured.

    ``passages_by_kb`` maps knowledge base name to a list of plain dicts
    (``text``, ``source_path``, ``heading``, ``score``) rather than
    ``aida.knowledge.rag.retrieval.RetrievedPassage`` objects directly, so
    this event stays a plain, JSON-serializable dataclass like every other
    ``AgentEvent`` (PLAN.md hard rule 6) without ``aida.core.events``
    needing to import ``aida.knowledge``'s dataclass shapes — the same
    "typed internally, plain dicts on the event" boundary
    ``ToolCallStarted.arguments`` already draws.
    """

    passages_by_kb: dict[str, list[dict[str, Any]]]

    to_dict = _base_dict


@dataclass(frozen=True)
class ContextTrimmed:
    """Emitted by ``aida.cli.chat.ChatSession.send()`` (B7) when
    ``_trim_context`` actually dropped history to fit
    ``AppConfig.max_context_tokens`` — before this, trimming happened
    silently (a log line only), so a user watching a long session slowly
    lose earlier turns had no visibility into it at all, and no way to
    tell "context got tight" from "the model just forgot".

    ``dropped_turns`` is how many whole turns (see
    ``aida.core.context.trim_history``) were cut this time.
    ``estimated_tokens`` is the *post*-trim estimate — what the sent
    history now costs, per ``aida.core.context.estimate_tokens`` (now
    tool-call-argument-aware) — so a frontend can show "trimmed to ~N
    tokens" rather than just "trimmed".

    ``summarized``/``summary_tokens`` (PLAN.md §1.3, planning/
    context_management.md §3.4): whether the dropped turns were replaced by
    a compaction summary rather than plain-discarded — ``False``/``0`` for
    a compaction call that itself failed (fell back to the pre-compaction
    plain-drop behavior) or wasn't attempted. Added as new fields with
    defaults rather than changing the meaning of the existing two, so every
    site that already constructs or reads a ``ContextTrimmed`` (CLI, GUI,
    tests) keeps working unchanged; both display sites just need wording
    that distinguishes "summarized N turns" from "dropped N turns".

    ``shrunk_tool_results`` is how many old tool results (or long tool-call
    arguments) were replaced by short stubs this time, which happens before
    any turn is dropped; it can be the only change (``dropped_turns == 0``).
    ``over_budget`` means that after every step the history is still over
    ``budget_tokens``, usually because the current turn alone is that big;
    the frontend should say so, since the provider may refuse the next
    request. Emitted at most once per turn when nothing else changed."""

    dropped_turns: int
    estimated_tokens: int
    summarized: bool = False
    summary_tokens: int = 0
    shrunk_tool_results: int = 0
    over_budget: bool = False
    budget_tokens: int = 0
    #: The estimate before this compaction; 0 when not known.
    tokens_before: int = 0

    to_dict = _base_dict

    def describe(self, *, include_warning: bool = True) -> str:
        """One plain-text line for a frontend: what changed and, when it
        applies and ``include_warning`` is set, that the history is still
        too large."""
        parts: list[str] = []
        if self.shrunk_tool_results:
            noun = "result" if self.shrunk_tool_results == 1 else "results"
            parts.append(f"shortened {self.shrunk_tool_results} old tool {noun}")
        if self.dropped_turns:
            turn_word = "turn" if self.dropped_turns == 1 else "turns"
            if self.summarized:
                parts.append(
                    f"summarized {self.dropped_turns} old {turn_word} into "
                    f"~{self.summary_tokens:,} tokens"
                )
            else:
                parts.append(f"dropped {self.dropped_turns} old {turn_word}")
        text = "; ".join(parts)
        if text and self.tokens_before:
            text = f"{text} (~{self.tokens_before:,} → ~{self.estimated_tokens:,} tokens)"
        elif text:
            text = f"{text} (~{self.estimated_tokens:,} tokens now)"
        if self.over_budget and include_warning:
            warning = (
                f"The conversation is still ~{self.estimated_tokens:,} tokens, over the "
                f"~{self.budget_tokens:,}-token budget, and the current turn can't be reduced "
                "further. The model may refuse the next request: start a new conversation, or "
                "continue from a progress file one task per message."
            )
            text = f"{text}. {warning}" if text else warning
        return text


@dataclass(frozen=True)
class SteeringMessageDelivered:
    """A message the user typed *while a turn was already running* has just
    been handed to the model.

    User request: "when agent is working, user has no chance for input to
    the process... in Claude app user can type content in and it gets
    included in message in next turn, so I can tell agent what I forgot."
    ``AgentLoop`` drains its queue between round trips (never mid-tool-call,
    never mid-stream), appends the text as an ordinary ``role="user"``
    message, and emits this so a frontend can show *when* the interjection
    actually landed rather than pretending it was sent instantly. Queued
    text that the turn ends before reaching is never delivered and never
    emits this — see ``AgentLoop.take_undelivered_messages``.
    """

    text: str

    to_dict = _base_dict


@dataclass(frozen=True)
class TurnNudged:
    """``AgentLoop`` added a message of its own and kept the turn going,
    instead of ending it on a reply that did not finish the job.

    ``reason`` is ``"length"`` (the reply was cut off at the output token
    limit before it made a tool call), ``"stall"`` (a short reply announced
    a next step, "Writing the report now.", but made no tool call) or
    ``"loop"`` (the turn keeps repeating calls, errors or near-identical
    scripts without progress, ``aida.core.loop_detection``). ``text``
    is the ``role="user"`` message that was appended; it is part of the
    conversation from here on, so a frontend shows it like a user message,
    marked as sent by AIDA.
    """

    reason: str
    text: str

    to_dict = _base_dict


@dataclass(frozen=True)
class ConversationTitled:
    """The conversation has just been (re)named from its own content.

    Emitted by ``aida.core.session.ChatSession.send`` after a turn ends —
    once for the first real title, then only on the occasional re-check
    that actually produces a different name (see ``aida.core.titling``).
    A re-check that answers "the current title still fits" emits nothing,
    so this event always means "the name in front of the user just
    changed" and a frontend can act on it unconditionally.

    Never emitted for a conversation whose title a person typed: those are
    locked in the database and skipped entirely.
    """

    conversation_id: str
    title: str

    to_dict = _base_dict


@dataclass(frozen=True)
class AgentError:
    """A terminal error for the current ``complete()``/``run()`` call.

    ``layer`` names which subsystem failed — ``"provider"``, ``"mcp"``,
    ``"core"``, ``"ui"``, ... — this is the "diagnostics are a feature"
    requirement from PLAN.md §7/§11: error messages must say *which* layer
    failed.
    """

    layer: str
    message: str
    detail: str | None = None
    #: Set by a provider when the failure looks transient — the endpoint
    #: was busy, overloaded, rate-limited, briefly unreachable, or (the ANL
    #: Argo pattern) answered with an upstream error such as "Permission
    #: ... denied on resource" that goes away on the next attempt. It only
    #: says "the same request might succeed if sent again";
    #: ``aida.providers.retry.complete_with_retries`` decides whether that
    #: is actually safe (nothing of the turn shown yet) and how long to
    #: wait. Defaults to ``False`` so every error that predates this field
    #: — and every non-provider error — is never retried.
    retryable: bool = False

    to_dict = _base_dict


@dataclass(frozen=True)
class ProviderRetrying:
    """The provider call just failed with a transient error and is about to
    be sent again after ``delay_seconds``.

    Informational, not terminal — unlike ``AgentError`` the stream carries
    on. Emitted by ``aida.providers.retry.complete_with_retries`` so a
    frontend can say "Argo is busy, retrying (2/5)" instead of showing a
    spinner that has silently stopped making progress. ``attempt`` is the
    1-based number of the attempt that *failed*; ``max_attempts`` is the
    total the policy allows (so the next try is ``attempt + 1`` of
    ``max_attempts``). ``message``/``detail`` are the failed attempt's
    ``AgentError`` fields, verbatim.
    """

    attempt: int
    max_attempts: int
    delay_seconds: float
    message: str
    detail: str | None = None

    to_dict = _base_dict


AgentEvent = (
    TextStarted
    | TextDelta
    | TextFinished
    | ToolCallStarted
    | ToolCallFinished
    | ImageArtifactCreated
    | FileArtifactCreated
    | MessageFinished
    | UsageInfo
    | RetrievalPerformed
    | ContextTrimmed
    | SteeringMessageDelivered
    | TurnNudged
    | ConversationTitled
    | ProviderRetrying
    | AgentError
)

__all__ = [
    "AgentError",
    "AgentEvent",
    "ContextTrimmed",
    "REASONING_TAIL_CHARS",
    "ConversationTitled",
    "FileArtifactCreated",
    "ImageArtifactCreated",
    "MessageFinished",
    "ProviderRetrying",
    "RetrievalPerformed",
    "SteeringMessageDelivered",
    "TurnNudged",
    "TextDelta",
    "TextFinished",
    "TextStarted",
    "ToolCallFinished",
    "ToolCallStarted",
    "UsageInfo",
]
