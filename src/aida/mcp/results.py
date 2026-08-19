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
                              -> an additional ``JsonArtifact``
"""

from __future__ import annotations

import base64

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


def convert_result(result: CallToolResult) -> list[Artifact]:
    """Convert every content block (and ``structuredContent``, if present)
    in an MCP tool result into AIDA artifacts, in order."""
    artifacts: list[Artifact] = [_convert_block(block) for block in result.content]
    if result.structuredContent is not None:
        artifacts.append(JsonArtifact(data=result.structuredContent))
    return artifacts


__all__ = ["convert_result"]
