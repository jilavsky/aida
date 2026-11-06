"""Transient provider failures are resent instead of costing the user a turn.

The ANL Argo proxy, when busy, answers with an error *inside* a 200 stream
("unexpected provider error", or a Vertex "Permission ... denied on
resource ...") that the SDKs' own HTTP-status retry never sees. Argo
support's guidance is to retry; ``aida.providers.retry`` does, bounded,
cancel-aware, and never after part of the reply was already shown.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Callable

import pytest
from anthropic import AnthropicError, AuthenticationError
from openai import APIError as OpenAIAPIError

from aida.core.agent import AgentLoop
from aida.core.events import (
    AgentError,
    AgentEvent,
    MessageFinished,
    ProviderRetrying,
    TextDelta,
    TextFinished,
    TextStarted,
)
from aida.providers.anthropic_ import AnthropicProvider
from aida.providers.base import (
    CompletionSettings,
    LLMProvider,
    Message,
    ToolSchema,
    is_transient_failure,
    is_transport_exception,
)
from aida.providers.openai_compat import OpenAICompatProvider
from aida.providers.retry import RetryPolicy, complete_with_retries

_ARGO_PERMISSION = (
    "Permission 'aiplatform.endpoints.predict' denied on resource "
    "'//aiplatform.googleapis.com/projects/x/locations/us-east5/publishers/anthropic/models/y' "
    "(or it may not exist)."
)

_FAST = RetryPolicy(max_attempts=4, initial_delay_seconds=1.0, max_delay_seconds=4.0, jitter=0.0)


# --- classification ----------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "text"),
    [
        (None, _ARGO_PERMISSION),
        (403, _ARGO_PERMISSION),
        (None, "An unexpected error occurred while streaming"),
        (529, "Overloaded"),
        (503, "Service Unavailable"),
        (429, "rate_limit_error: Number of request tokens has exceeded your per-minute rate limit"),
        (500, "Internal server error"),
        (200, "upstream returned an error, please try again"),
    ],
)
def test_transient_failures_are_retryable(status_code: int | None, text: str):
    assert is_transient_failure(status_code, text) is True


@pytest.mark.parametrize(
    ("status_code", "text"),
    [
        (401, "authentication_error: access denied"),
        (400, "invalid_request_error: messages.1: tool_result block without tool_use"),
        (200, "Error code: 400 - {'type': 'invalid_request_error', 'message': 'bad'}"),
        (None, "prompt is too long: 250000 tokens > 200000 maximum"),
        (404, "model not found"),
        (422, "unprocessable"),
    ],
)
def test_request_problems_are_not_retryable(status_code: int | None, text: str):
    assert is_transient_failure(status_code, text) is False


def test_transport_exceptions_vs_programming_errors():
    assert is_transport_exception(TimeoutError("read timed out"))
    assert is_transport_exception(ConnectionResetError())
    assert not is_transport_exception(TypeError("create() got an unexpected keyword"))
    assert not is_transport_exception(KeyError("x"))


def test_backoff_doubles_and_is_capped():
    policy = RetryPolicy(initial_delay_seconds=2.0, max_delay_seconds=10.0, jitter=0.0)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 10.0]


def test_jitter_stays_within_its_band():
    policy = RetryPolicy(initial_delay_seconds=4.0, jitter=0.25)
    rng = random.Random(1)
    for _ in range(50):
        assert 3.0 <= policy.delay_for(1, rng) <= 5.0


# --- providers mark their errors ---------------------------------------


class _RaisingCreate:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __call__(self, **kwargs):
        raise self.error


async def _collect(provider: LLMProvider) -> list[AgentEvent]:
    return [
        e
        async for e in provider.complete(
            [Message(role="user", content="hi")], [], CompletionSettings(model="m")
        )
    ]


@pytest.mark.asyncio
async def test_anthropic_marks_an_argo_in_stream_error_retryable():
    provider = AnthropicProvider(model="m", api_key="k")
    provider._client.messages.create = _RaisingCreate(AnthropicError(_ARGO_PERMISSION))
    (error,) = await _collect(provider)
    assert isinstance(error, AgentError)
    assert error.message == "unexpected provider error"
    assert error.retryable is True


@pytest.mark.asyncio
async def test_anthropic_never_retries_bad_credentials():
    exc = AuthenticationError.__new__(AuthenticationError)
    Exception.__init__(exc, "invalid x-api-key")
    exc.status_code = 401
    provider = AnthropicProvider(model="m", api_key="k")
    provider._client.messages.create = _RaisingCreate(exc)
    (error,) = await _collect(provider)
    assert error.message == "authentication failed"
    assert error.retryable is False


@pytest.mark.asyncio
async def test_anthropic_never_retries_a_programming_error():
    provider = AnthropicProvider(model="m", api_key="k")
    provider._client.messages.create = _RaisingCreate(TypeError("unexpected keyword 'foo'"))
    (error,) = await _collect(provider)
    assert error.retryable is False


@pytest.mark.asyncio
async def test_openai_marks_an_argo_in_stream_error_retryable():
    exc = OpenAIAPIError.__new__(OpenAIAPIError)
    Exception.__init__(exc, _ARGO_PERMISSION)
    provider = OpenAICompatProvider(model="m", api_key="k")
    provider._client.chat.completions.create = _RaisingCreate(exc)
    (error,) = await _collect(provider)
    assert error.message == "unexpected provider error"
    assert error.retryable is True


# --- the retry wrapper -------------------------------------------------


class _Attempt:
    """What one ``complete()`` call of ``_ScriptedProvider`` does."""

    def __init__(
        self, *, text: str = "", partial: str = "", error: str | None = None, retryable=True
    ):
        self.text = text
        self.partial = partial
        self.error = error
        self.retryable = retryable


class _ScriptedProvider(LLMProvider):
    def __init__(self, attempts: list[_Attempt]) -> None:
        self.attempts = list(attempts)
        self.calls = 0

    async def complete(
        self, messages: list[Message], tools: list[ToolSchema], settings: CompletionSettings
    ) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        attempt = self.attempts.pop(0)
        yield TextStarted(message_id="m")
        if attempt.partial:
            yield TextDelta(message_id="m", text=attempt.partial)
        if attempt.error is not None:
            yield AgentError(layer="provider", message=attempt.error, retryable=attempt.retryable)
            return
        yield TextDelta(message_id="m", text=attempt.text)
        yield TextFinished(message_id="m", text=attempt.text)
        yield MessageFinished(message_id="m", stop_reason="stop")

    async def ping(self) -> bool:
        return True


class _FakeSleep:
    def __init__(self, on_sleep: Callable[[], None] | None = None) -> None:
        self.total = 0.0
        self.on_sleep = on_sleep

    async def __call__(self, seconds: float) -> None:
        self.total += seconds
        if self.on_sleep is not None:
            self.on_sleep()


async def _run(provider, **kwargs) -> list[AgentEvent]:
    return [
        e
        async for e in complete_with_retries(
            provider,
            [Message(role="user", content="hi")],
            [],
            CompletionSettings(model="m"),
            **kwargs,
        )
    ]


@pytest.mark.asyncio
async def test_busy_then_success_is_retried_transparently():
    provider = _ScriptedProvider(
        [_Attempt(error="unexpected provider error"), _Attempt(error="busy"), _Attempt(text="ok")]
    )
    sleep = _FakeSleep()
    events = await _run(provider, policy=_FAST, sleep=sleep)

    assert provider.calls == 3
    assert not [e for e in events if isinstance(e, AgentError)]
    retries = [e for e in events if isinstance(e, ProviderRetrying)]
    assert [(r.attempt, r.max_attempts, r.delay_seconds) for r in retries] == [
        (1, 4, 1.0),
        (2, 4, 2.0),
    ]
    assert retries[0].message == "unexpected provider error"
    assert sleep.total == pytest.approx(3.0)
    assert [e.text for e in events if isinstance(e, TextFinished)] == ["ok"]


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts_and_says_so():
    provider = _ScriptedProvider([_Attempt(error="unexpected provider error")] * 4)
    events = await _run(provider, policy=_FAST, sleep=_FakeSleep())

    assert provider.calls == 4
    final = events[-1]
    assert isinstance(final, AgentError)
    assert final.message == "unexpected provider error (gave up after 4 attempts)"
    assert len([e for e in events if isinstance(e, ProviderRetrying)]) == 3


@pytest.mark.asyncio
async def test_non_retryable_error_is_reported_at_once():
    provider = _ScriptedProvider([_Attempt(error="authentication failed", retryable=False)])
    events = await _run(provider, policy=_FAST, sleep=_FakeSleep())

    assert provider.calls == 1
    assert events[-1] == AgentError(layer="provider", message="authentication failed")


@pytest.mark.asyncio
async def test_never_replays_a_reply_that_was_partly_shown():
    provider = _ScriptedProvider([_Attempt(partial="Half an ans", error="connection failed")])
    events = await _run(provider, policy=_FAST, sleep=_FakeSleep())

    assert provider.calls == 1
    assert isinstance(events[-1], AgentError)
    assert events[-1].message == "connection failed"


@pytest.mark.asyncio
async def test_stop_during_backoff_ends_the_wait():
    cancelled = {"flag": False}

    def press_stop() -> None:
        cancelled["flag"] = True

    provider = _ScriptedProvider([_Attempt(error="busy"), _Attempt(text="never")])
    sleep = _FakeSleep(on_sleep=press_stop)
    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=30.0, jitter=0.0)
    events = await _run(
        provider, policy=policy, sleep=sleep, should_cancel=lambda: cancelled["flag"]
    )

    assert provider.calls == 1
    assert sleep.total < 1.0  # one poll slice, not the full 30 s
    assert events[-1] == AgentError(layer="core", message="cancelled")


# --- the agent loop uses it --------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_rides_out_a_busy_provider(monkeypatch):
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("aida.providers.retry.asyncio.sleep", instant)
    provider = _ScriptedProvider([_Attempt(error=_ARGO_PERMISSION), _Attempt(text="answer")])
    loop = AgentLoop(provider, CompletionSettings(model="m"), retry_policy=_FAST)
    messages = [Message(role="user", content="hi")]

    events = [e async for e in loop.run(messages)]

    assert any(isinstance(e, ProviderRetrying) for e in events)
    assert not [e for e in events if isinstance(e, AgentError)]
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "answer"
