"""Retry transient provider failures — the ANL Argo "busy" case.

Provider layer (no Qt, no UI). Wraps one ``LLMProvider.complete()`` call:
when the provider ends its stream with an ``AgentError`` marked
``retryable`` (see ``aida.providers.base.is_transient_failure``), and
nothing of the turn has reached the caller yet, the same request is sent
again after an exponential backoff, with a ``ProviderRetrying`` event in
between so a frontend can say what is happening.

Why this exists on top of the SDKs' own ``max_retries``: the ``anthropic``
and ``openai`` SDKs retry only what they see as an HTTP status (429, 5xx,
connection errors) *before* a stream starts. The Argo proxy reports a busy
upstream as an error event *inside* a 200 stream ("unexpected provider
error", or a Vertex "Permission ... denied on resource ..."), which the SDK
surfaces as a plain exception and never retries. Argo support's guidance
is for clients to retry these; Claude/OpenAI's own apps do the same.

Rules the wrapper keeps:

* **Never replay a turn the user has already seen part of.** Once any
  content event (text, a tool call, usage) has gone out, a failure is
  reported as-is — resending would duplicate text or re-announce tool
  calls. A bare ``TextStarted`` does not count: the GUI creates no bubble
  until the first ``TextDelta``.
* **Bounded.** ``RetryPolicy.max_attempts`` total tries, delays doubling
  from ``initial_delay_seconds`` up to ``max_delay_seconds``, with jitter
  so several AIDA instances behind one proxy do not retry in lockstep.
* **Stop still stops.** The wait is sliced and ``should_cancel`` is
  polled, so pressing Stop during a backoff ends the turn within a
  fraction of a second instead of after the full delay.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from aida.config.logging_setup import get_logger
from aida.core.events import AgentError, AgentEvent, ProviderRetrying, TextStarted
from aida.providers.base import CompletionSettings, LLMProvider, Message, ToolSchema

_logger = get_logger("provider")

#: How often a backoff wait checks ``should_cancel`` (seconds).
_CANCEL_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, and how patiently, to resend a transient failure.

    Attributes:
        max_attempts: Total tries including the first; ``1`` disables
            retrying entirely.
        initial_delay_seconds: Wait before the second try.
        max_delay_seconds: Cap on any single wait.
        multiplier: Growth factor between consecutive waits.
        jitter: Fractional spread applied to each wait (``0.25`` = ±25 %).
    """

    max_attempts: int = 5
    initial_delay_seconds: float = 2.0
    max_delay_seconds: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.25

    def delay_for(self, failed_attempt: int, rng: random.Random | None = None) -> float:
        """Seconds to wait after attempt number ``failed_attempt`` (1-based)
        failed. With the defaults: ~2, 4, 8, 16 s — about half a minute of
        patience in total before the error is shown."""
        base = self.initial_delay_seconds * self.multiplier ** max(failed_attempt - 1, 0)
        base = min(base, self.max_delay_seconds)
        if self.jitter <= 0:
            return base
        spread = (rng or random).uniform(-self.jitter, self.jitter)
        return max(0.0, base * (1.0 + spread))


#: The agent loop's policy: patient enough to ride out a busy Argo backend
#: (~30 s in total), short enough that a real outage is reported promptly.
DEFAULT_RETRY_POLICY = RetryPolicy()

#: For background helper calls (context compaction) that already have a
#: fallback when they fail — not worth holding a turn for half a minute.
SHORT_RETRY_POLICY = RetryPolicy(max_attempts=3, initial_delay_seconds=2.0, max_delay_seconds=8.0)

#: No retries at all — the provider's first answer is final.
NO_RETRY_POLICY = RetryPolicy(max_attempts=1)


async def _wait_unless_cancelled(
    delay: float,
    should_cancel: Callable[[], bool] | None,
    sleep: Callable[[float], Awaitable[object]],
) -> bool:
    """Sleep ``delay`` seconds in short slices; ``True`` if cancelled."""
    remaining = delay
    while remaining > 0:
        if should_cancel is not None and should_cancel():
            return True
        step = min(_CANCEL_POLL_SECONDS, remaining)
        await sleep(step)
        remaining -= step
    return should_cancel is not None and should_cancel()


async def complete_with_retries(
    provider: LLMProvider,
    messages: list[Message],
    tools: list[ToolSchema],
    settings: CompletionSettings,
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    should_cancel: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> AsyncIterator[AgentEvent]:
    """``provider.complete(...)``, resent on transient failure.

    Same contract as ``LLMProvider.complete``: an async generator of
    ``AgentEvent`` that ends with at most one ``AgentError`` and never
    raises, so it is a drop-in replacement at every call site. Adds
    ``ProviderRetrying`` events between attempts.

    Args:
        provider: The provider to call.
        messages: Conversation so far (not modified).
        tools: Tool schemas offered to the model.
        settings: Per-request settings.
        policy: Attempt count and backoff.
        should_cancel: Polled during backoff; when it returns ``True`` the
            wait is abandoned and ``AgentError(layer="core",
            message="cancelled")`` is yielded.
        sleep: Injected for tests; ``asyncio.sleep`` otherwise.
        rng: Injected for deterministic jitter in tests.

    Yields:
        The provider's events, ``ProviderRetrying`` before each resend, and
        — if every attempt failed — the last ``AgentError``, its message
        suffixed with how many attempts were made.
    """
    attempt = 0
    while True:
        attempt += 1
        content_sent = False
        failure: AgentError | None = None
        # aclosing so an early exit (Stop pressed, caller closes us) tears
        # down the provider's HTTP stream right away — see AgentLoop.
        async with contextlib.aclosing(provider.complete(messages, tools, settings)) as stream:
            async for event in stream:
                if isinstance(event, AgentError):
                    failure = event
                    break
                if not isinstance(event, TextStarted):
                    content_sent = True
                yield event

        if failure is None:
            return

        if not failure.retryable or content_sent or attempt >= policy.max_attempts:
            if attempt > 1:
                failure = dataclasses.replace(
                    failure, message=f"{failure.message} (gave up after {attempt} attempts)"
                )
            elif failure.retryable and content_sent:
                _logger.info(
                    "transient provider error after part of the reply was shown; not retrying: %s",
                    failure.detail or failure.message,
                )
            yield failure
            return

        delay = policy.delay_for(attempt, rng)
        _logger.warning(
            "provider attempt %d/%d failed (%s: %s); retrying in %.1fs",
            attempt,
            policy.max_attempts,
            failure.message,
            failure.detail,
            delay,
        )
        yield ProviderRetrying(
            attempt=attempt,
            max_attempts=policy.max_attempts,
            delay_seconds=delay,
            message=failure.message,
            detail=failure.detail,
        )
        if await _wait_unless_cancelled(delay, should_cancel, sleep):
            yield AgentError(layer="core", message="cancelled")
            return


__all__ = [
    "DEFAULT_RETRY_POLICY",
    "NO_RETRY_POLICY",
    "SHORT_RETRY_POLICY",
    "RetryPolicy",
    "complete_with_retries",
]
