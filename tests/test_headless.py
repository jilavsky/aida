from __future__ import annotations

import asyncio

from aida.core.confirmation import ConfirmationRequest
from aida.core.headless import build_headless_confirm_callback


def _run(coro):
    return asyncio.run(coro)


def test_denies_everything_by_default():
    confirm = build_headless_confirm_callback(yes_in_allowed=False)
    assert (
        _run(
            confirm(
                ConfirmationRequest(
                    action="write", path="/allowed/x", detail="", in_allowed_roots=True
                )
            )
        )
        is False
    )


def test_yes_in_allowed_approves_in_bounds_requests():
    confirm = build_headless_confirm_callback(yes_in_allowed=True)
    assert (
        _run(
            confirm(
                ConfirmationRequest(
                    action="write", path="/allowed/x", detail="", in_allowed_roots=True
                )
            )
        )
        is True
    )


def test_yes_in_allowed_never_approves_outside_allowed_roots():
    """--yes-in-allowed narrows the workspace's own safety mode; it must
    never widen it to the "always confirm no matter what" categories."""
    confirm = build_headless_confirm_callback(yes_in_allowed=True)
    assert (
        _run(
            confirm(
                ConfirmationRequest(
                    action="write", path="/elsewhere/x", detail="", in_allowed_roots=False
                )
            )
        )
        is False
    )


def test_yes_in_allowed_never_approves_a_url_fetch():
    confirm = build_headless_confirm_callback(yes_in_allowed=True)
    assert (
        _run(
            confirm(
                ConfirmationRequest(
                    action="fetch_url", path="http://example.com", detail="", in_allowed_roots=False
                )
            )
        )
        is False
    )


def test_tool_call_denied_when_not_preapproved():
    confirm = build_headless_confirm_callback(
        yes_in_allowed=True, preapproved_tools={"server__other_tool"}
    )
    assert (
        _run(
            confirm(
                ConfirmationRequest(action="tool_call", path="server__dangerous_tool", detail="")
            )
        )
        is False
    )


def test_tool_call_approved_when_preapproved_by_exact_namespaced_name():
    confirm = build_headless_confirm_callback(
        yes_in_allowed=False, preapproved_tools={"server__dangerous_tool"}
    )
    assert (
        _run(
            confirm(
                ConfirmationRequest(action="tool_call", path="server__dangerous_tool", detail="")
            )
        )
        is True
    )


def test_tool_call_ignores_yes_in_allowed_entirely():
    """A tool_call confirmation is never approved just because
    --yes-in-allowed was passed — only explicit pre-approval by name
    counts, regardless of in_allowed_roots (which is meaningless here)."""
    confirm = build_headless_confirm_callback(yes_in_allowed=True, preapproved_tools=set())
    assert (
        _run(confirm(ConfirmationRequest(action="tool_call", path="server__tool", detail="")))
        is False
    )


def test_no_preapproved_tools_denies_every_tool_call():
    confirm = build_headless_confirm_callback(yes_in_allowed=True)
    assert (
        _run(confirm(ConfirmationRequest(action="tool_call", path="server__tool", detail="")))
        is False
    )
