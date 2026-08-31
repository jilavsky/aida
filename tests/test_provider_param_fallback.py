"""A model that refuses a sampling parameter must not cost the user a turn.

Newer Claude models answer ``temperature`` with a 400 ("`temperature` is
deprecated for this model"), and the ANL Argo proxy relays that as its own
200 carrying the upstream error envelope. Both providers recover by
dropping the one offending parameter and retrying, then remembering it for
the rest of the session — see ``AnthropicProvider._param_to_drop``.

Same approach as test_anthropic_provider_ping.py: a real provider with a
fake ``_client``, so what's under test is the provider's own retry logic,
not the SDK's wire format.
"""

from __future__ import annotations

from typing import Any

import pytest
from anthropic import APIStatusError as AnthropicAPIStatusError
from openai import APIStatusError as OpenAIAPIStatusError

from aida.core.events import AgentError
from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.base import CompletionSettings, Message, unsupported_request_param
from aida.providers.openai_compat import OpenAICompatProvider

_DEPRECATED = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': '`temperature` is deprecated for this model.'}}"
)


def _status_error(cls: type, status_code: int, message: str) -> Exception:
    """An SDK APIStatusError with just the two attributes the retry path
    reads (``status_code`` and ``str(exc)``) — building a real one needs an
    httpx request/response pair that adds nothing to this test."""
    exc = cls.__new__(cls)
    Exception.__init__(exc, message)
    exc.status_code = status_code
    return exc


class _FakeStream:
    """An empty but well-formed stream — enough for the success path."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self._events = events or []

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event

        return gen()


class _FakeCreate:
    """Raises ``error`` on every call until ``temperature`` is gone from the
    kwargs, recording what it was called with."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise self.error
        return _FakeStream()


# --- the pure helper ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "`temperature` is deprecated for this model.",
        "Unsupported value: 'temperature' does not support 0.7 with this model.",
        "unknown parameter: temperature",
    ],
)
def test_recognizes_a_rejected_temperature(text: str):
    assert unsupported_request_param(text, {"model": "m", "temperature": 0.7}) == "temperature"


def test_ignores_an_error_about_something_else():
    """A 400 about the request body itself must surface to the user, not be
    silently retried with a field removed."""
    text = "messages.3.content: unsupported content block type 'image'"
    assert unsupported_request_param(text, {"model": "m", "temperature": 0.7, "messages": []}) is None


def test_never_drops_a_parameter_that_changes_the_request():
    text = "max_tokens is not supported for this model"
    assert unsupported_request_param(text, {"max_tokens": 100, "tools": []}) is None


def test_ignores_a_parameter_we_did_not_send():
    text = "`top_k` is deprecated for this model."
    assert unsupported_request_param(text, {"model": "m", "temperature": 0.7}) is None


# --- the providers -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 200])
async def test_anthropic_retries_without_temperature(status_code: int):
    """status 200 covers the Argo proxy, which answers with its own 200
    wrapping the upstream 400."""
    provider = AnthropicProvider(model="claude-opus-5", api_key="k")
    create = _FakeCreate(_status_error(AnthropicAPIStatusError, status_code, _DEPRECATED))
    provider._client.messages.create = create

    events = [
        event
        async for event in provider.complete(
            [Message(role="user", content="hi")], [], CompletionSettings(model="claude-opus-5")
        )
    ]

    assert not [e for e in events if isinstance(e, AgentError)]
    assert len(create.calls) == 2
    assert "temperature" in create.calls[0]
    assert "temperature" not in create.calls[1]


@pytest.mark.asyncio
async def test_anthropic_remembers_the_rejection_for_later_turns():
    provider = AnthropicProvider(model="claude-opus-5", api_key="k")
    create = _FakeCreate(_status_error(AnthropicAPIStatusError, 400, _DEPRECATED))
    provider._client.messages.create = create
    settings = CompletionSettings(model="claude-opus-5")

    for _ in range(2):
        async for _event in provider.complete([Message(role="user", content="hi")], [], settings):
            pass

    # 2 calls for the first turn (fail + retry), 1 for the second.
    assert len(create.calls) == 3
    assert "temperature" not in create.calls[2]


@pytest.mark.asyncio
async def test_anthropic_surfaces_an_unrelated_400_without_retrying():
    provider = AnthropicProvider(model="claude-opus-5", api_key="k")
    error = _status_error(AnthropicAPIStatusError, 400, "messages: at least one message is required")

    async def always_fails(**kwargs: Any):
        raise error

    provider._client.messages.create = always_fails

    events = [
        event
        async for event in provider.complete([], [], CompletionSettings(model="claude-opus-5"))
    ]

    assert [e.message for e in events if isinstance(e, AgentError)] == ["API error (400)"]


@pytest.mark.asyncio
async def test_openai_compat_retries_without_temperature():
    provider = OpenAICompatProvider(model="gpt-x", base_url="http://localhost:1234/v1", api_key="k")
    create = _FakeCreate(
        _status_error(OpenAIAPIStatusError, 400, "Unsupported value: 'temperature' is not supported")
    )
    provider._client.chat.completions.create = create

    events = [
        event
        async for event in provider.complete(
            [Message(role="user", content="hi")], [], CompletionSettings(model="gpt-x")
        )
    ]

    assert not [e for e in events if isinstance(e, AgentError)]
    assert len(create.calls) == 2
    assert "temperature" not in create.calls[1]
