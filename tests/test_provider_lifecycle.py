"""Regression coverage for the aclose() bug found during Phase 2 manual
end-to-end verification: asyncio.run() closing the event loop before an
SDK-backed provider's HTTP client had a chance to close produced a spurious
GeneratorExit/RuntimeError traceback on `aida chat` exit. Fixed by giving
every provider an explicit aclose() that callers (ChatSession) must invoke.
"""

from __future__ import annotations

import pytest

from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.mock import MockProvider, MockTurn
from aida.providers.openai_compat import OpenAICompatProvider


@pytest.mark.asyncio
async def test_mock_provider_aclose_is_a_harmless_noop():
    provider = MockProvider([MockTurn(text="hi")])
    await provider.aclose()  # must not raise


@pytest.mark.asyncio
async def test_openai_compat_provider_aclose_closes_underlying_client(monkeypatch):
    provider = OpenAICompatProvider(model="m", base_url="http://x", api_key="k")
    calls = []

    async def fake_close():
        calls.append("closed")

    monkeypatch.setattr(provider._client, "close", fake_close)
    await provider.aclose()
    assert calls == ["closed"]


@pytest.mark.asyncio
async def test_anthropic_provider_aclose_closes_underlying_client(monkeypatch):
    provider = AnthropicProvider(model="m", base_url="http://x", api_key="k")
    calls = []

    async def fake_close():
        calls.append("closed")

    monkeypatch.setattr(provider._client, "close", fake_close)
    await provider.aclose()
    assert calls == ["closed"]
