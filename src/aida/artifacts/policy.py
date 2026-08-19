"""What the LLM sees for each artifact type — Phase 3's explicit, tested
text policy (never the GUI's problem: images/files reach the model as a
short description, never as raw base64 flooding the context window).

This is deliberately its own small module so the policy is easy to find,
read end-to-end, and unit test without any MCP or agent-loop machinery.
"""

from __future__ import annotations

import json

from aida.artifacts.base import (
    Artifact,
    FileArtifact,
    ImageArtifact,
    JsonArtifact,
    TableArtifact,
    TextArtifact,
)

DEFAULT_MAX_CHARS = 4000
_TRUNCATION_NOTE = "\n... [truncated]"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE


def describe_for_model(artifact: Artifact, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Render one artifact as the text a provider's tool-result message
    should contain. Never includes raw binary/base64 data."""

    if isinstance(artifact, TextArtifact):
        return _truncate(artifact.text, max_chars)

    if isinstance(artifact, ImageArtifact):
        size = len(artifact.data) if artifact.data else 0
        location = f", saved to {artifact.path}" if artifact.path else ""
        return f"[image artifact {artifact.id}: {artifact.mime_type}, {size} bytes{location}]"

    if isinstance(artifact, FileArtifact):
        location = f" at {artifact.path}" if artifact.path else " (not yet saved)"
        mime = f", {artifact.mime_type}" if artifact.mime_type else ""
        return f"[file artifact {artifact.id}{mime}, saved{location}]"

    if isinstance(artifact, JsonArtifact):
        try:
            text = json.dumps(artifact.data, indent=2, default=str)
        except TypeError:
            text = str(artifact.data)
        return _truncate(text, max_chars)

    if isinstance(artifact, TableArtifact):
        return _truncate(_render_table(artifact, max_chars), max_chars)

    raise TypeError(f"no text policy for artifact type {type(artifact).__name__}")


def _render_table(artifact: TableArtifact, max_chars: int) -> str:
    lines = [" | ".join(artifact.columns), " | ".join("---" for _ in artifact.columns)]
    total_rows = len(artifact.rows)
    for i, row in enumerate(artifact.rows):
        lines.append(" | ".join(str(cell) for cell in row))
        if sum(len(line) for line in lines) > max_chars:
            remaining = total_rows - i - 1
            if remaining > 0:
                lines.append(f"... [{remaining} more rows truncated]")
            break
    return "\n".join(lines)


__all__ = ["DEFAULT_MAX_CHARS", "describe_for_model"]
