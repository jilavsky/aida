"""Headless confirmation policy shared by ``aida run``, ``aida workflow
run``, and the scheduler (planning/phase10_scheduling_design.md §3.1) — no
frontend it drives has a human to ask, so it must always resolve, never
block, and must never grant more than it is explicitly told to.

Two independent gates, matching the two kinds of confirmation
``aida.core.confirmation.ConfirmationRequest`` carries:

- An MCP ``"tool_call"`` request (a per-tool "confirm before run" flag,
  ``aida.mcp.manager``) is approved only if its namespaced tool name
  (``request.path``, already ``server__tool`` by the time it reaches a
  callback) was explicitly pre-approved for this run. There is no flag
  that approves every such tool at once — a workflow author names exactly
  which tools they accept responsibility for running unattended.
- Everything else (a filesystem/URL confirmation from ``SafetyGuard``/
  ``default_web_tools``) is approved only if ``yes_in_allowed`` was passed
  *and* ``request.in_allowed_roots`` is ``True`` — i.e. this is exactly the
  kind of request that ``"confirm"`` mode alone would have asked about, not
  one of the "always confirm no matter what" categories (outside every
  allowed root, an unlisted shell command, any URL fetch). ``--yes-in-
  allowed`` narrows a workspace's own safety mode; it can never widen it.
"""

from __future__ import annotations

from aida.core.confirmation import ConfirmationRequest


def build_headless_confirm_callback(*, yes_in_allowed: bool, preapproved_tools: set[str] | None = None):
    """Returns a ``ConfirmCallback`` (see ``aida.core.confirmation``) that
    never blocks: every request is answered immediately from the two rules
    above, using only what the caller passed in — no terminal prompt, no
    GUI dialog, nothing that could hang an unattended run."""
    approved_tools = frozenset(preapproved_tools or ())

    async def _confirm(request: ConfirmationRequest) -> bool:
        if request.action == "tool_call":
            return request.path in approved_tools
        return yes_in_allowed and request.in_allowed_roots

    return _confirm


__all__ = ["build_headless_confirm_callback"]
