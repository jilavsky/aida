"""Native (non-MCP) tools the agent loop can call directly.

Phase 2 ships exactly one: ``get_current_time`` — its whole job is to prove
the tool-call round-trip (model requests a tool -> loop executes it -> result
fed back -> model uses it in the final answer) works end-to-end *without*
MCP, which arrives in Phase 3. Real workspace/document tools come in later
phases (PLAN.md §10).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aida.artifacts.base import Artifact
from aida.providers.base import ToolSchema


@dataclass
class ToolResult:
    """What a native tool hands back to the agent loop.

    ``artifacts`` (Phase 3): any typed image/file/table/json artifacts the
    tool produced, e.g. converted from an MCP result via
    ``aida.mcp.results.convert_result``. ``content`` is still what actually
    gets fed back to the model as the tool-result message (normally the
    text-policy description of each artifact) — ``artifacts`` is what lets
    the agent loop emit ``ImageArtifactCreated``/``FileArtifactCreated``
    events for the frontend, per PLAN.md hard rule 3 ("typed results
    throughout" — never just guessing from a string).
    """

    content: Any
    is_error: bool = False
    artifacts: list[Artifact] = field(default_factory=list)


ToolFunc = Callable[[dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class NativeTool:
    """A tool schema (offered to the model) paired with its implementation."""

    schema: ToolSchema
    func: ToolFunc


async def _get_current_time(_arguments: dict[str, Any]) -> ToolResult:
    now = datetime.now(UTC)
    return ToolResult(content={"utc_iso": now.isoformat(), "unix": int(now.timestamp())})


GET_CURRENT_TIME = NativeTool(
    schema=ToolSchema(
        name="get_current_time",
        description="Get the current date and time in UTC.",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    func=_get_current_time,
)


def default_native_tools() -> dict[str, NativeTool]:
    """The native tool set the CLI harness registers by default in Phase 2."""
    return {GET_CURRENT_TIME.schema.name: GET_CURRENT_TIME}


__all__ = ["GET_CURRENT_TIME", "NativeTool", "ToolFunc", "ToolResult", "default_native_tools"]
