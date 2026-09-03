"""Tests for aida.core.confirmation.RememberingConfirm — the "Allow for
this chat" wrapper (Phase 11) that turns a tri-state, interactive
RawConfirmCallback into the plain bool-returning ConfirmCallback every
SafetyGuard/McpManager consumer already expects."""

from __future__ import annotations

import pytest

from aida.core.confirmation import (
    REMEMBERABLE_ACTIONS,
    ConfirmAnswer,
    ConfirmationRequest,
    RememberingConfirm,
)


def _raw(answer: ConfirmAnswer):
    calls: list[ConfirmationRequest] = []

    async def _confirm(request: ConfirmationRequest) -> ConfirmAnswer:
        calls.append(request)
        return answer

    return _confirm, calls


@pytest.mark.asyncio
async def test_allow_once_does_not_get_remembered():
    raw, calls = _raw(ConfirmAnswer.ALLOW_ONCE)
    remembering = RememberingConfirm(raw)
    request = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write?", remember_scope="/tmp")

    assert await remembering(request) is True
    assert await remembering(request) is True
    assert len(calls) == 2, "ALLOW_ONCE must ask again next time, not be remembered"


@pytest.mark.asyncio
async def test_allow_for_chat_is_remembered_for_same_action_and_scope():
    raw, calls = _raw(ConfirmAnswer.ALLOW_FOR_CHAT)
    remembering = RememberingConfirm(raw)
    first = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write a?", remember_scope="/tmp")
    second = ConfirmationRequest(action="write", path="/tmp/b.txt", detail="Write b?", remember_scope="/tmp")

    assert await remembering(first) is True
    assert await remembering(second) is True
    assert len(calls) == 1, "a second file in the same remembered folder must not re-prompt"


@pytest.mark.asyncio
async def test_remembered_approval_does_not_cover_a_different_action():
    raw, calls = _raw(ConfirmAnswer.ALLOW_FOR_CHAT)
    remembering = RememberingConfirm(raw)
    write_request = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write?", remember_scope="/tmp")
    delete_request = ConfirmationRequest(action="delete", path="/tmp/a.txt", detail="Delete?", remember_scope="/tmp")

    await remembering(write_request)
    await remembering(delete_request)
    assert len(calls) == 2, "approving a write must not silently approve a delete in the same folder"


@pytest.mark.asyncio
async def test_remembered_approval_does_not_cover_a_different_scope():
    raw, calls = _raw(ConfirmAnswer.ALLOW_FOR_CHAT)
    remembering = RememberingConfirm(raw)
    first = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write?", remember_scope="/tmp")
    other_folder = ConfirmationRequest(
        action="write", path="/var/tmp/a.txt", detail="Write?", remember_scope="/var/tmp"
    )

    await remembering(first)
    await remembering(other_folder)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_deny_is_not_remembered_and_returns_false():
    raw, calls = _raw(ConfirmAnswer.DENY)
    remembering = RememberingConfirm(raw)
    request = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write?", remember_scope="/tmp")

    assert await remembering(request) is False
    assert await remembering(request) is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_remember_scope_none_is_never_remembered():
    """fetch_url's ConfirmationRequest never sets remember_scope — even an
    ALLOW_FOR_CHAT answer must not silence the next call."""
    raw, calls = _raw(ConfirmAnswer.ALLOW_FOR_CHAT)
    remembering = RememberingConfirm(raw)
    request = ConfirmationRequest(action="fetch_url", path="https://example.com", detail="Fetch?")

    await remembering(request)
    await remembering(request)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_action_outside_the_allowlist_is_never_remembered_even_with_a_scope():
    """Defense in depth: even if a caller mistakenly attached a
    remember_scope to a non-allowlisted action, REMEMBERABLE_ACTIONS still
    blocks it from being cached."""
    assert "fetch_url" not in REMEMBERABLE_ACTIONS
    raw, calls = _raw(ConfirmAnswer.ALLOW_FOR_CHAT)
    remembering = RememberingConfirm(raw)
    request = ConfirmationRequest(
        action="fetch_url", path="https://example.com", detail="Fetch?", remember_scope="https://example.com"
    )

    await remembering(request)
    await remembering(request)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_raw_callback_returning_a_bool_raises_type_error():
    """RememberingConfirm must only ever wrap a real tri-state
    RawConfirmCallback — a legacy bool-returning callback would otherwise
    misbehave silently (False is not ConfirmAnswer.DENY is True)."""

    async def _bad_raw(_request: ConfirmationRequest) -> bool:
        return False

    remembering = RememberingConfirm(_bad_raw)
    request = ConfirmationRequest(action="write", path="/tmp/a.txt", detail="Write?", remember_scope="/tmp")

    with pytest.raises(TypeError):
        await remembering(request)
