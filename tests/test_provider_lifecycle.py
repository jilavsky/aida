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


# ---------------------------------------------------------------------------
# B1 (vision) / B3 (prompt caching): CompletionSettings actually reaches the
# real SDK call kwargs, not just the pure translation functions unit-tested
# in test_provider_translation.py. Each ``complete()`` here is monkeypatched
# at the SDK client boundary (same pattern as the aclose() tests above) so
# no network call is made; the fake just records the kwargs it was given.
# ---------------------------------------------------------------------------


class _EmptyAsyncStream:
    """Minimal stand-in for the SDK's streaming response — an async
    iterator that immediately ends, so ``complete()`` yields no events but
    still exercises the kwargs-building code on the way there."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_anthropic_provider_complete_caches_system_and_tools():
    from aida.providers.base import CompletionSettings, Message, ToolSchema

    provider = AnthropicProvider(model="claude-x", base_url="http://x", api_key="k")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _EmptyAsyncStream()

    provider._client.messages.create = fake_create

    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]
    tools = [ToolSchema(name="get_time", description="Get time", parameters={"type": "object"})]
    settings = CompletionSettings(model="claude-x")

    events = [e async for e in provider.complete(messages, tools, settings)]

    assert events == []
    assert captured["system"] == [
        {"type": "text", "text": "be helpful", "cache_control": {"type": "ephemeral"}}
    ]
    assert captured["tools"][-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_anthropic_provider_complete_attaches_image_pixels_only_when_supports_vision(tmp_path):
    import base64

    from aida.providers.base import CompletionSettings, ImageRef, Message

    png_path = tmp_path / "tiny.png"
    png_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )

    provider = AnthropicProvider(model="claude-x", base_url="http://x", api_key="k")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _EmptyAsyncStream()

    provider._client.messages.create = fake_create

    messages = [Message(role="user", content="what is this?", images=[ImageRef(path=str(png_path))])]

    # Vision disabled (the default) -> plain text content, no image block.
    [e async for e in provider.complete(messages, [], CompletionSettings(model="claude-x"))]
    assert captured["messages"][0]["content"] == "what is this?"

    # Vision enabled -> the image's pixels are attached.
    [
        e
        async for e in provider.complete(
            messages, [], CompletionSettings(model="claude-x", supports_vision=True)
        )
    ]
    content = captured["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image"


@pytest.mark.asyncio
async def test_openai_compat_provider_complete_threads_supports_vision(tmp_path):
    import base64

    from aida.providers.base import CompletionSettings, ImageRef, Message

    png_path = tmp_path / "tiny.png"
    png_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )

    provider = OpenAICompatProvider(model="local-model", base_url="http://x", api_key="k")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _EmptyAsyncStream()

    provider._client.chat.completions.create = fake_create

    messages = [Message(role="user", content="what is this?", images=[ImageRef(path=str(png_path))])]

    [
        e
        async for e in provider.complete(
            messages, [], CompletionSettings(model="local-model", supports_vision=True)
        )
    ]
    content = captured["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
