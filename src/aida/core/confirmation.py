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
from enum import Enum


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
    uses ``action="tool_call"`` and ``path="server__tool"`` (the namespaced
    tool name) rather than inventing a parallel request type.

    ``in_allowed_roots`` (Phase 10, headless automation): ``True`` exactly
    when this confirmation exists *only* because the workspace's safety
    mode is ``"confirm"`` — i.e. it is the kind of request that would have
    sailed through without asking in ``"relaxed"`` mode. ``False`` for the
    "always confirm no matter what" categories: a path outside every
    allowed root, or a shell command that isn't on the command allowlist.
    An MCP ``"tool_call"`` confirmation (a per-tool "confirm before run"
    flag, unrelated to allowed roots) leaves this at its default and a
    headless caller should not consult it for that action — it identifies
    those by ``action == "tool_call"`` instead. Defaults to ``True`` so
    every pre-existing ``ConfirmationRequest(...)`` construction (tests
    included) keeps working unchanged.
    """

    action: str
    path: str
    detail: str
    in_allowed_roots: bool = True
    #: "Allow for this chat" (Phase 11): the key a ``RememberingConfirm``
    #: should remember this approval under, paired with ``action`` — e.g.
    #: a folder path for a file-safety gate, a cwd for a shell command, or
    #: an exact namespaced tool name for an MCP ``"tool_call"``. ``None``
    #: means "never remember this" — the caller (``web.py``'s ``fetch_url``)
    #: deliberately never sets it, so that always-confirms unconditionally
    #: regardless of what a user clicks elsewhere. Defaults to ``None`` so
    #: every pre-existing ``ConfirmationRequest(...)`` construction (tests
    #: included) keeps working unchanged.
    remember_scope: str | None = None


ConfirmCallback = Callable[[ConfirmationRequest], Awaitable[bool]]


class ConfirmAnswer(Enum):
    """What an interactive, tri-state raw confirmation prompt (``cli_confirm``,
    ``ChatBridge``'s interactive method) actually returns — richer than the
    plain bool every other ``ConfirmCallback`` implementer (``SafetyGuard``,
    ``McpManager``, tests, ``build_headless_confirm_callback``) uses, so
    that a human can distinguish "yes, just this once" from "yes, and stop
    asking me about this for the rest of the chat"."""

    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_FOR_CHAT = "allow_for_chat"


#: The type of a raw, interactive confirmation prompt — distinct from
#: ``ConfirmCallback`` (bool-returning), which every non-interactive
#: consumer (``SafetyGuard``, ``McpManager``, tests, headless mode) still
#: uses unchanged. Only ``RememberingConfirm`` ever calls a
#: ``RawConfirmCallback`` directly.
RawConfirmCallback = Callable[[ConfirmationRequest], Awaitable[ConfirmAnswer]]

#: Actions eligible for "Allow for this chat". Deliberately an allowlist,
#: not a denylist of the one excluded action (``fetch_url``) — a future
#: action type someone adds must opt in by name here, rather than relying
#: on everyone remembering not to opt `fetch_url` in. This is what backs
#: docs/safety-and-permissions.md's "fetch_url... every single call asks,
#: unconditionally, in every mode" guarantee: even if a future caller
#: mistakenly attached a ``remember_scope`` to a ``fetch_url`` request, it
#: still could never be remembered because ``"fetch_url"`` is absent here.
REMEMBERABLE_ACTIONS = frozenset({"read", "write", "delete", "run_script", "execute", "tool_call"})


class RememberingConfirm:
    """Wraps a ``RawConfirmCallback`` (tri-state) into a plain
    ``ConfirmCallback`` (bool) — the shape ``SafetyGuard``/``McpManager``
    already expect, so neither needs to change how it calls
    ``confirm_callback``. Remembers ``ALLOW_FOR_CHAT`` answers in an
    in-memory set for its own lifetime only: never written to disk, never
    shared across instances. One instance should live exactly as long as
    one chat/session (one ``ChatBridge``, one CLI process) — construct a
    fresh one per session, not a shared global, so "remember for this
    chat" can't leak into the next one."""

    def __init__(self, raw: RawConfirmCallback) -> None:
        self._raw = raw
        self._approved: set[tuple[str, str]] = set()

    async def __call__(self, request: ConfirmationRequest) -> bool:
        rememberable = request.remember_scope is not None and request.action in REMEMBERABLE_ACTIONS
        key = (request.action, request.remember_scope)
        if rememberable and key in self._approved:
            return True

        answer = await self._raw(request)
        if not isinstance(answer, ConfirmAnswer):
            # A RawConfirmCallback that returns a plain bool would silently
            # misbehave here: `False is not ConfirmAnswer.DENY` is True, so
            # a denial would be reported as approved. Fail loudly instead —
            # this class must only ever wrap a real tri-state callback.
            raise TypeError(f"raw confirm callback must return ConfirmAnswer, got {answer!r}")

        if answer is ConfirmAnswer.ALLOW_FOR_CHAT and rememberable:
            self._approved.add(key)
        return answer is not ConfirmAnswer.DENY


async def deny_all(_request: ConfirmationRequest) -> bool:
    """The safe default when no real callback is wired in — e.g. a
    headless/test caller that never intended to allow anything beyond
    what's already implicitly allowed."""
    return False


__all__ = [
    "REMEMBERABLE_ACTIONS",
    "ConfirmAnswer",
    "ConfirmCallback",
    "ConfirmationDenied",
    "ConfirmationRequest",
    "RawConfirmCallback",
    "RememberingConfirm",
    "deny_all",
]
