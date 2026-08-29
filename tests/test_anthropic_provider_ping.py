"""PLAN.md §1.5 — AnthropicProvider.ping() no longer spends a real token on
every doctor run / "Test" click unless it has to.

Constructs a real AnthropicProvider (no network — its ``_client`` is
replaced with lightweight fakes) rather than mocking HTTP transport, same
reasoning as test_provider_translation.py's module docstring: ``ping()``'s
own logic (which client method it calls, and when it falls back) is what's
under test here, not the SDK's wire format.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from anthropic import APIConnectionError, AuthenticationError, NotFoundError

from aida.providers.anthropic_ import AnthropicProvider


def _provider() -> AnthropicProvider:
    return AnthropicProvider(model="claude-sonnet", api_key="test-key")


def _fake_error(cls: type[Exception]) -> Exception:
    """A bare instance of one of the anthropic SDK's exception types,
    without needing a real httpx request/response to construct it — `raise`
    only needs the type to match what ``ping()`` catches, not a populated
    message/response."""
    return cls.__new__(cls)


@pytest.mark.asyncio
async def test_ping_uses_models_list_not_a_paid_completion():
    provider = _provider()
    provider._client.models.list = AsyncMock(return_value=None)
    provider._client.messages.create = AsyncMock(side_effect=AssertionError("must not be called"))

    assert await provider.ping() is True
    provider._client.models.list.assert_awaited_once()
    provider._client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_ping_falls_back_to_a_message_when_models_endpoint_is_missing():
    """A base_url proxy (e.g. the ANL Argo proxy) may only implement the
    Messages API — NotFoundError on /v1/models must not be reported as
    "unreachable" when the proxy is actually fine."""
    provider = _provider()
    provider._client.models.list = AsyncMock(side_effect=_fake_error(NotFoundError))
    provider._client.messages.create = AsyncMock(return_value=None)

    assert await provider.ping() is True
    provider._client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_ping_reports_false_on_a_real_failure_without_a_second_call():
    """An actual auth/connection failure is a real negative — no point
    spending a token on a paid fallback call that would fail the same way."""
    provider = _provider()
    provider._client.models.list = AsyncMock(side_effect=_fake_error(AuthenticationError))
    provider._client.messages.create = AsyncMock(return_value=None)

    assert await provider.ping() is False
    provider._client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_ping_reports_false_when_the_fallback_call_also_fails():
    provider = _provider()
    provider._client.models.list = AsyncMock(side_effect=_fake_error(NotFoundError))
    provider._client.messages.create = AsyncMock(side_effect=_fake_error(APIConnectionError))

    assert await provider.ping() is False
