"""A tiny, real MCP stdio server used only by AIDA's own tests.

Launched as an actual subprocess (real stdio JSON-RPC, not a mock object) by
``aida.mcp.server.McpServerHandle`` in integration tests — this is Phase 3's
"mock-mcp fixture server" task. Deliberately covers every content-type case
the artifact-conversion layer needs to handle: plain text, a small PNG image,
JSON-shaped data, a multi-part (text + image) result, a tool that raises, and
a tool that hangs forever (for timeout-handling tests).
"""

from __future__ import annotations

import asyncio
import base64
import os

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ImageContent, TextContent

mcp = FastMCP("mock-mcp")

# A minimal valid 1x1 transparent PNG (67 bytes), used so image-decoding
# tests exercise real, decodable image bytes rather than placeholder junk.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TINY_PNG_BYTES = base64.b64decode(_TINY_PNG_B64)


@mcp.tool()
def echo_text(message: str) -> str:
    """Echo back the given message as plain text."""
    return f"echo: {message}"


@mcp.tool()
def get_image() -> Image:
    """Return a tiny 1x1 PNG image."""
    return Image(data=TINY_PNG_BYTES, format="png")


@mcp.tool()
def get_json_data() -> dict:
    """Return a small JSON-shaped object."""
    return {"sample_id": "S001", "rg": 34.2, "valid": True}


@mcp.tool()
def get_multi_part() -> list[TextContent | ImageContent]:
    """Return a result with both a text block and an image block, to
    exercise multi-part-result handling."""
    return [
        TextContent(type="text", text="Here is the plot:"),
        ImageContent(type="image", data=_TINY_PNG_B64, mimeType="image/png"),
    ]


@mcp.tool()
def always_fails() -> str:
    """Always raises, to exercise the tool-level error path."""
    raise RuntimeError("intentional failure for testing")


@mcp.tool()
async def hang_forever() -> str:
    """Never returns, to exercise per-call timeout handling."""
    await asyncio.sleep(3600)
    return "unreachable"


@mcp.tool()
def crash_process() -> str:
    """Kill this server process immediately, to exercise
    restart-after-crash handling (no clean JSON-RPC response is ever sent)."""
    os._exit(1)  # deliberate hard kill, not a normal process exit
    return "unreachable"  # pragma: no cover


if __name__ == "__main__":
    mcp.run(transport="stdio")
