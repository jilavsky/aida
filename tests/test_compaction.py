"""PLAN.md §1.3 / planning/context_management.md §3.4 — compaction:

summarize-then-replace instead of plain-discarding the oldest turns once a
conversation is over budget, plus the manual ``ChatSession.compact_now()``
trigger (the CLI's ``/compact`` and the GUI's "Compact Conversation").

Uses ``MockProvider`` throughout — note that the compaction summarization
call consumes one scripted ``MockTurn`` of its own, separate from any
"real" reply a test also expects (see ``ChatSession._compact_context``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.config.settings import ProviderProfile, Settings, load_settings
from aida.core.events import ContextTrimmed
from aida.core.session import ChatSession
from aida.providers.base import Message
from aida.providers.mock import MockProvider, MockTurn


def _settings_with_profile(name="mock-profile", **overrides) -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(
        name=name, kind="openai_compat", model="mock-model", **overrides
    )
    return settings


def _add_old_turns(session: ChatSession, n: int, *, size: int = 2000) -> None:
    for i in range(n):
        session.messages.append(Message(role="user", content=f"old question {i} " + "x" * size))
        session.messages.append(Message(role="assistant", content="old answer " + "y" * size))


@pytest.mark.asyncio
async def test_compact_now_summarizes_and_replaces_dropped_turns(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="- fit run_042.dat: Rg=32.4")]),
    )

    session = ChatSession(settings, "mock-profile")
    _add_old_turns(session, 10)
    before = len(session.messages)

    event = await session.compact_now()

    assert event is not None
    assert isinstance(event, ContextTrimmed)
    assert event.summarized is True
    assert event.dropped_turns > 0
    assert event.summary_tokens > 0
    assert len(session.messages) < before
    summary_messages = [m for m in session.messages if "Summary of earlier conversation" in m.content]
    assert len(summary_messages) == 1
    assert "Rg=32.4" in summary_messages[0].content
    await session.aclose()


@pytest.mark.asyncio
async def test_compact_now_returns_none_when_not_enough_history(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    session.messages.append(Message(role="user", content="hi"))

    event = await session.compact_now()

    assert event is None
    await session.aclose()


@pytest.mark.asyncio
async def test_compact_now_falls_back_to_plain_drop_when_summarization_call_is_exhausted(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Compaction failing must never fail the user's turn (context_management.md
    §3.4): an empty script means the summarization call itself gets
    "MockProvider script exhausted" back as an AgentError — compact_now
    must still fall back to today's plain-discard behavior rather than
    raising or leaving history untouched."""
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    _add_old_turns(session, 10)
    before = len(session.messages)

    event = await session.compact_now()

    assert event is not None
    assert event.summarized is False
    assert event.summary_tokens == 0
    assert event.dropped_turns > 0
    assert len(session.messages) < before
    assert not any("Summary of earlier conversation" in m.content for m in session.messages)
    await session.aclose()


@pytest.mark.asyncio
async def test_send_falls_back_to_plain_drop_when_compaction_provider_call_errors(
    monkeypatch, aida_home: Path, records_home: Path
):
    """The automatic path (send()) gets the same fallback: the
    summarization call errors (an explicit MockTurn(error=...), not just an
    exhausted script), the actual turn's own provider call still succeeds
    from the next scripted turn, and the resulting ContextTrimmed reports a
    plain drop, not a summary."""
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 200
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(error="rate limited"), MockTurn(text="ok")]),
    )

    session = ChatSession(settings, "mock-profile")
    _add_old_turns(session, 40)

    events = [e async for e in session.send("the new question")]
    trim_events = [e for e in events if isinstance(e, ContextTrimmed)]
    assert len(trim_events) == 1
    assert trim_events[0].summarized is False
    assert trim_events[0].dropped_turns > 0
    assert session.messages[-2].content == "the new question"
    assert session.messages[-1].content == "ok"
    await session.aclose()


@pytest.mark.asyncio
async def test_context_fullness_reports_used_tokens_and_budget(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    used_before, budget = session.context_fullness()
    assert budget > 0

    session.messages.append(Message(role="user", content="x" * 4000))
    used_after, budget_after = session.context_fullness()
    assert used_after > used_before
    assert budget_after == budget  # the budget itself doesn't move with history size
    await session.aclose()


@pytest.mark.asyncio
async def test_context_fullness_budget_is_zero_when_trimming_disabled(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 0
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    _, budget = session.context_fullness()
    assert budget == 0
    await session.aclose()


@pytest.mark.asyncio
async def test_context_fullness_prefers_the_profiles_own_context_window(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Per-profile context_window (PLAN.md §1.3) takes precedence over the
    global AppConfig.max_context_tokens when both are set."""
    settings = _settings_with_profile(context_window=50_000)
    settings.app.max_context_tokens = 999_999_999
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    _, budget = session.context_fullness()
    assert budget < 999_999_999  # must be derived from context_window, not the huge global default
    await session.aclose()
