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

`AgentError` can appear at any point and always ends the stream for that
`complete()`/`run()` call — it is not just informational.
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


@dataclass(frozen=True)
class MessageFinished:
    """One provider turn (one call to ``LLMProvider.complete``) has ended.

    ``stop_reason`` is provider-normalized: ``"stop"`` (final answer),
    ``"tool_calls"`` (model wants tool results before continuing),
    ``"length"`` (hit max_tokens), or ``"cancelled"``.
    """

    message_id: str
    stop_reason: str

    to_dict = _base_dict


@dataclass(frozen=True)
class UsageInfo:
    """Token accounting for one provider turn, when the provider reports it."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int | None = None

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
    | AgentError
)

__all__ = [
    "AgentError",
    "AgentEvent",
    "FileArtifactCreated",
    "ImageArtifactCreated",
    "MessageFinished",
    "TextDelta",
    "TextFinished",
    "TextStarted",
    "ToolCallFinished",
    "ToolCallStarted",
    "UsageInfo",
]
