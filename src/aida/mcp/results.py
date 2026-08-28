"""Convert an MCP ``CallToolResult`` into AIDA's typed artifacts.

This is the keystone of Phase 3 (PLAN.md): an ``ImageContent`` block must
become a real ``ImageArtifact`` with decoded bytes immediately — it must
never be flattened into a text string anywhere on this path.

Content-block mapping (verified against the real ``mcp`` SDK's content-block
union, not guessed — see ``tests/mock_mcp_server.py`` for a real server that
exercises every case):

- ``TextContent``            -> ``TextArtifact``
- ``ImageContent``           -> ``ImageArtifact`` (base64-decoded here)
- ``AudioContent``           -> ``FileArtifact`` (base64-decoded; audio has
                                 no dedicated artifact type, and "some bytes
                                 with a mime type" is exactly what
                                 ``FileArtifact`` is for)
- ``ResourceLink``           -> ``FileArtifact`` with only a URI, no local
                                 bytes (AIDA doesn't fetch resource links in
                                 Phase 3 — that's out of scope here)
- ``EmbeddedResource``       -> ``TextArtifact`` if the embedded resource is
                                 text, ``FileArtifact`` (decoded) if it's a blob
- ``result.structuredContent`` (separate from ``content``, when present)
                              -> an additional ``JsonArtifact``, *unless* it
                                 merely repeats a text block already in
                                 ``content`` (see ``_duplicates_text_block``)
"""

from __future__ import annotations

import base64
import json
from typing import Any

from mcp.types import CallToolResult, ContentBlock

from aida.artifacts.base import Artifact, FileArtifact, ImageArtifact, JsonArtifact, TextArtifact


def _convert_block(block: ContentBlock) -> Artifact:
    block_type = getattr(block, "type", None)

    if block_type == "text":
        return TextArtifact(text=block.text)

    if block_type == "image":
        return ImageArtifact(data=base64.b64decode(block.data), mime_type=block.mimeType)

    if block_type == "audio":
        return FileArtifact(
            data=base64.b64decode(block.data),
            mime_type=block.mimeType,
            filename=f"audio.{(block.mimeType or 'audio/bin').split('/')[-1]}",
        )

    if block_type == "resource_link":
        return FileArtifact(path=None, mime_type=block.mimeType, filename=block.name)

    if block_type == "resource":
        resource = block.resource
        text = getattr(resource, "text", None)
        if text is not None:
            return TextArtifact(text=text)
        blob = getattr(resource, "blob", None)
        mime_type = getattr(resource, "mimeType", None)
        return FileArtifact(
            data=base64.b64decode(blob) if blob else b"",
            mime_type=mime_type,
        )

    # Unknown/future content-block type: keep the tool call from crashing,
    # but be honest that we don't know how to render it richly.
    return TextArtifact(text=f"[unsupported MCP content block: {block_type!r}]")


def _duplicates_text_block(structured: Any, artifacts: list[Artifact]) -> bool:
    """Whether ``structured`` says nothing the already-converted ``content``
    blocks don't.

    FastMCP (what pyirena-mcp and most Python MCP servers are built on)
    returns a structured tool result *twice*: once JSON-serialized into a
    ``TextContent`` block, and again verbatim as ``structuredContent`` —
    the spec's own backwards-compatibility rule, since older clients only
    read ``content``. AIDA converted both, so every such tool result
    reached the model as the same payload rendered twice in a row: double
    the tool-result tokens on every single call, on the exact path
    (pyIrena analysis, UC3/UC4) where tool results are largest and calls
    are most frequent, plus a model left to wonder whether two adjacent
    near-identical blobs are actually two different things.

    Deliberately conservative — only an *exact* match is treated as a
    duplicate, so a server that genuinely puts different information in
    the two places still gets both through:

    - exactly one text artifact, whose text parses as JSON equal to
      ``structured``; or
    - the same, against ``structured["result"]`` when ``result`` is
      ``structured``'s only key — FastMCP's own wrapper for a tool that
      returns a non-object (a list, a number), where ``content``'s text is
      the bare value and ``structuredContent`` is the wrapped one.
    """
    texts = [a for a in artifacts if isinstance(a, TextArtifact)]
    if len(texts) != 1 or len(artifacts) != 1:
        return False
    try:
        parsed = json.loads(texts[0].text)
    except ValueError:
        return False
    if parsed == structured:
        return True
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return parsed == structured["result"]
    return False


def convert_result(result: CallToolResult) -> list[Artifact]:
    """Convert every content block (and ``structuredContent``, if present)
    in an MCP tool result into AIDA artifacts, in order."""
    artifacts: list[Artifact] = [_convert_block(block) for block in result.content]
    if result.structuredContent is not None and not _duplicates_text_block(
        result.structuredContent, artifacts
    ):
        artifacts.append(JsonArtifact(data=result.structuredContent))
    return artifacts


__all__ = ["convert_result"]
