"""The human-in-the-loop confirmation channel: one generic ``async def
confirm(ConfirmationRequest) -> bool`` shape shared by every subsystem that
needs to ask the user "is this okay?" without adding a bidirectional
request/reply protocol to ``aida.core.events`` (today's event stream is a
strictly one-directional async generator — core yields, frontend consumes).

Originally defined only in ``aida.workspace.safety`` (Phase 6's
allowed-folders model). Phase 7 needs the same channel for a second,
unrelated caller — a per-tool "confirm before run" flag on an MCP tool
(``aida.mcp.manager``) — so a confirm-flagged tool call pops the exact same
GUI modal / CLI prompt a file-safety confirmation already does, with no new
UI plumbing. That forced the move: ``aida.workspace.workspaces`` already
imports ``aida.mcp.groups``, so ``aida.mcp.manager`` importing anything from
``aida.workspace.safety`` directly would create a real import cycle
(``aida.workspace``'s package ``__init__`` pulls in ``aida.workspace.workspaces``
-> ``aida.mcp.groups`` while ``aida.mcp``'s own ``__init__`` is still
mid-import) — the same trap ``unique_destination`` hit and was moved to
``aida.config.paths`` to avoid. This module has zero AIDA dependencies of
its own, so both ``aida.workspace.safety`` (which re-exports everything
here for existing importers) and ``aida.mcp.manager`` can depend on it
without a cycle either way.

The CLI's callback (``aida.cli.chat.cli_confirm``) blocks on a real
terminal prompt; the GUI's (``aida.ui.qt.bridge.ChatBridge._confirm``)
shows a real modal ``QMessageBox`` on the Qt thread and bridges the answer
back to the background asyncio loop thread via ``asyncio.wrap_future``.
Both reach the user in the exact interface they're already looking at.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class ConfirmationDenied(Exception):
    """Raised when a confirmation-gated action is declined — or when no
    real ``confirm_callback`` was wired in at all (``deny_all``), since
    silently proceeding without anyone able to answer would defeat the
    whole point of asking. This is a plain exception, not a new
    ``ToolResult`` error path: ``aida.core.agent.AgentLoop`` already wraps
    every tool-func call in a generic ``try/except Exception`` and turns it
    into an error-flagged ``ToolResult`` (see its docstring) — no agent-loop
    changes are needed for this to surface cleanly as a normal tool error."""


@dataclass(frozen=True)
class ConfirmationRequest:
    """What a ``confirm_callback`` is asked to approve or deny.

    ``action``/``path`` were named for the original file-safety use case
    (``"read"``/``"write"``/``"delete"`` and a filesystem path) but the
    shape is generic enough for a second caller: an MCP per-tool confirm
    uses ``action="tool_call"`` and ``path="server.tool"`` (the namespaced
    tool name) rather than inventing a parallel request type.
    """

    action: str
    path: str
    detail: str


ConfirmCallback = Callable[[ConfirmationRequest], Awaitable[bool]]


async def deny_all(_request: ConfirmationRequest) -> bool:
    """The safe default when no real callback is wired in — e.g. a
    headless/test caller that never intended to allow anything beyond
    what's already implicitly allowed."""
    return False


__all__ = ["ConfirmCallback", "ConfirmationDenied", "ConfirmationRequest", "deny_all"]
