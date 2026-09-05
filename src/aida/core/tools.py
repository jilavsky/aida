"""Native (non-MCP) tools the agent loop can call directly.

Phase 2 ships exactly one: ``get_current_time`` — its whole job is to prove
the tool-call round-trip (model requests a tool -> loop executes it -> result
fed back -> model uses it in the final answer) works end-to-end *without*
MCP, which arrives in Phase 3. Real workspace/document tools come in later
phases (PLAN.md §10).
"""

from __future__ import annotations

import functools
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


def wrap_tool_errors(*expected: type[BaseException]) -> Callable[[ToolFunc], ToolFunc]:
    """Decorator factory (Phase 6): wraps a tool coroutine so any of
    ``expected`` exception types raised inside it become a normal
    ``ToolResult(is_error=True, content=str(exc))`` instead of propagating.

    ``AgentLoop._run_turns`` already has its own blanket ``try/except
    Exception`` one level up that turns *any* tool crash into an error
    result (see its docstring) — this decorator doesn't replace that, it's
    a stricter, self-documenting inner boundary for a tool's *expected*,
    named failure modes (a declined confirmation, a missing file, a
    timeout, ...) so the tool's contract is "always returns a ToolResult"
    and can be unit-tested by calling ``tool.func(...)`` directly, without
    needing a full ``AgentLoop`` harness just to see an expected failure
    turned into a result. Used by ``aida.workspace.files`` and
    ``aida.documents.tools``.
    """

    def decorator(func: ToolFunc) -> ToolFunc:
        @functools.wraps(func)
        async def wrapper(arguments: dict[str, Any]) -> ToolResult:
            try:
                return await func(arguments)
            except expected as exc:
                return ToolResult(content=str(exc), is_error=True)

        return wrapper

    return decorator


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


__all__ = [
    "GET_CURRENT_TIME",
    "NativeTool",
    "ToolFunc",
    "ToolResult",
    "default_native_tools",
    "wrap_tool_errors",
]
