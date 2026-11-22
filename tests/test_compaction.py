"""PLAN.md §1.3 / planning/completed/context_management.md §3.4 — compaction:

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
from tests.compaction_helpers import SummarizingMockProvider


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
    summary_messages = [
        m for m in session.messages if "Summary of earlier conversation" in m.content
    ]
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


# --- planning/completed/improvement_plan_2026-09.md §2: mid-turn context check —
# AgentLoop.before_round_trip, wired to ChatSession._trim_context -----------


@pytest.mark.asyncio
async def test_context_is_rechecked_mid_turn_not_only_before_it(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Before AgentLoop grew a before_round_trip hook, _trim_context ran
    exactly once, before the turn even started (_run_turn's own call,
    still present) — a single tool result big enough to blow the budget on
    its own went completely unchecked until the *next* user message.

    History here is comfortably under budget when the turn starts (the
    pre-turn check finds nothing to do) and only tips over once one huge
    tool result is appended mid-turn, before the second provider round
    trip — proving the recheck actually happens *during* the turn, not
    only at its edges."""
    from aida.core.tools import NativeTool, ToolResult
    from aida.providers.base import ToolSchema
    from aida.providers.mock import MockToolCall

    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # clamped up to MIN_HISTORY_BUDGET (8000 tokens)
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [
                MockTurn(tool_calls=[MockToolCall(name="huge_tool", id="call_1")]),
                MockTurn(text="done"),
            ]
        ),
    )

    async def _huge_tool(_args):
        return ToolResult(content="z" * 7_900)  # just under the result cap

    tools = {
        "huge_tool": NativeTool(
            schema=ToolSchema(name="huge_tool", description="", parameters={"type": "object"}),
            func=_huge_tool,
        )
    }
    session = ChatSession(settings, "mock-profile", tools=tools)
    _add_old_turns(session, 5, size=2500)  # ~25k chars, under the 8000-token budget

    events = [e async for e in session.send("trigger the tool")]

    tool_finished_index = next(
        i for i, e in enumerate(events) if type(e).__name__ == "ToolCallFinished"
    )
    trim_indices = [i for i, e in enumerate(events) if isinstance(e, ContextTrimmed)]
    assert trim_indices, "expected a mid-turn ContextTrimmed event once the huge result landed"
    assert trim_indices[0] > tool_finished_index
    await session.aclose()


@pytest.mark.asyncio
async def test_mid_turn_trim_does_not_stop_the_rest_of_the_turn_being_recorded(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """A mid-turn trim shortens the front of ``session.messages``. The
    persistence position used to be a plain index local to ``_run_turn``
    that the trim never adjusted, so everything after the trim (the tool
    result that triggered it and the final answer) existed in memory only
    and was missing from the DB and the transcript."""
    from aida.artifacts.store import ArtifactStore
    from aida.core.tools import NativeTool, ToolResult
    from aida.persistence.recorder import ConversationRecorder
    from aida.persistence.store import ConversationStore
    from aida.providers.base import ToolSchema
    from aida.providers.mock import MockToolCall

    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: SummarizingMockProvider(
            [
                MockTurn(tool_calls=[MockToolCall(name="huge_tool", id="call_1")]),
                MockTurn(text="done"),
            ]
        ),
    )

    async def _huge_tool(_args):
        return ToolResult(content="z" * 7_900)  # just under the result cap

    tools = {
        "huge_tool": NativeTool(
            schema=ToolSchema(name="huge_tool", description="", parameters={"type": "object"}),
            func=_huge_tool,
        )
    }
    recorder = ConversationRecorder(
        ConversationStore(tmp_path / "aida.db"),
        ArtifactStore(base_dir=tmp_path / "artifacts"),
        tmp_path / "records",
    )
    session = ChatSession(settings, "mock-profile", tools=tools, recorder=recorder)
    _add_old_turns(session, 5, size=2500)

    events = [e async for e in session.send("trigger the tool")]
    assert any(isinstance(e, ContextTrimmed) for e in events)

    stored = recorder.load_history()
    in_memory_turn = session.messages[-4:]
    assert [m.role for m in in_memory_turn] == ["user", "assistant", "tool", "assistant"]
    assert [(m.role, m.content) for m in stored[-4:]] == [
        (m.role, m.content) for m in in_memory_turn
    ]
    assert stored[-1].content == "done"
    await session.aclose()


@pytest.mark.asyncio
async def test_agent_loop_before_round_trip_hook_is_the_sessions_trim_context(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Direct wiring check, independent of the scenario above actually
    triggering a trim: session.loop must be constructed with its own
    _trim_context as before_round_trip, both at session start and after a
    /profile switch (aida.core.session.ChatSession.switch_profile)."""
    settings = _settings_with_profile()
    settings.providers.profiles["other-profile"] = ProviderProfile(
        name="other-profile", kind="openai_compat", model="mock-model"
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))

    session = ChatSession(settings, "mock-profile")
    assert session.loop._before_round_trip == session._trim_context

    await session.switch_profile("other-profile")
    assert session.loop._before_round_trip == session._trim_context
    await session.aclose()


# --- long autonomous runs (TODO §6, 2026-11-20) ------------------------------


def _agent_turn(session: ChatSession, rounds: int, *, result_chars: int = 20_000) -> None:
    """Append one user message and ``rounds`` tool rounds with big results."""
    from aida.providers.base import ToolCall

    session.messages.append(Message(role="user", content="review every proposal"))
    for i in range(rounds):
        call = ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"p{i}.pdf"})
        session.messages.append(Message(role="assistant", content="", tool_calls=[call]))
        session.messages.append(
            Message(
                role="tool",
                content=f"proposal {i} " + "z" * result_chars,
                tool_call_id=f"c{i}",
                name="read_file",
            )
        )


def _history_tokens(session: ChatSession) -> int:
    from aida.core.context import estimate_message_tokens

    return sum(estimate_message_tokens(m) for m in session.messages)


@pytest.mark.asyncio
async def test_one_huge_turn_goes_under_budget_by_shrinking_alone(
    monkeypatch, aida_home: Path, records_home: Path
):
    """The 2026-11-20 failure: one user message, 150 tool rounds. No turn can
    be dropped, so only shrinking old results can help — with no model call."""
    from aida.core.context import repair_tool_call_pairing

    provider = MockProvider([])
    settings = _settings_with_profile(context_window=128_000)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(settings, "mock-profile")
    _agent_turn(session, 150)
    budget = session._history_budget()
    assert _history_tokens(session) > budget

    event = await session._trim_context()

    assert event is not None
    assert event.shrunk_tool_results > 0
    assert event.dropped_turns == 0
    assert event.over_budget is False
    assert event.tokens_before > budget >= event.estimated_tokens
    assert _history_tokens(session) <= budget
    assert repair_tool_call_pairing(session.messages) == session.messages
    assert provider.calls == []
    await session.aclose()


@pytest.mark.asyncio
async def test_summary_is_not_resummarized_on_every_round_trip(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Regression for the logged loop ("239 message(s) -> 239 (1 turn(s)
    summarized)"): once only the summary is above the floor and the history
    is still over budget, repeated trims must not keep summarizing it."""
    from aida.core.context import compaction_summary_message

    provider = SummarizingMockProvider([])
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # clamped up to 8000 tokens
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(settings, "mock-profile")
    session.messages.append(compaction_summary_message("- run_042.dat: Rg=32.4"))
    for i in range(4):
        session.messages.append(Message(role="user", content=f"q{i} " + "x" * 5_000))
        session.messages.append(Message(role="assistant", content="ok"))
    session.messages[-2] = Message(role="user", content="current " + "x" * 40_000)

    events = [await session._trim_context() for _ in range(5)]

    # The first call folds the summary and the three droppable turns into one
    # new summary; after that only the oversized current turn is left.
    assert len(provider.summary_requests) == 1
    assert "Rg=32.4" in provider.summary_requests[0][0].content
    summaries = [m for m in session.messages if "Summary of earlier conversation" in m.content]
    assert len(summaries) == 1
    assert events[0] is not None and events[0].summarized and events[0].over_budget
    assert events[1:] == [None, None, None, None]
    await session.aclose()


@pytest.mark.asyncio
async def test_second_compaction_merges_the_previous_summary(
    monkeypatch, aida_home: Path, records_home: Path
):
    from aida.core.context import COMPACTION_MERGE_INSTRUCTION, compaction_summary_message

    provider = SummarizingMockProvider([], summary_text="- merged facts")
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(_settings_with_profile(), "mock-profile")
    session.messages.append(compaction_summary_message("- run_042.dat: Rg=32.4"))
    _add_old_turns(session, 8)

    event = await session.compact_now()

    assert event is not None and event.summarized
    assert len(provider.summary_requests) == 1
    request = provider.summary_requests[0][0].content
    assert COMPACTION_MERGE_INSTRUCTION in request and "Rg=32.4" in request
    summaries = [m for m in session.messages if "Summary of earlier conversation" in m.content]
    assert len(summaries) == 1
    assert "merged facts" in summaries[0].content
    system_count = sum(1 for m in session.messages if m.role == "system")
    assert session.messages.index(summaries[0]) == system_count  # first after system
    await session.aclose()


@pytest.mark.asyncio
async def test_compact_now_shrinks_inside_one_huge_current_turn(
    monkeypatch, aida_home: Path, records_home: Path
):
    """ "Compact Conversation" used to do nothing in a 2-turn conversation."""
    provider = MockProvider([])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(_settings_with_profile(), "mock-profile")
    _add_old_turns(session, 1)
    _agent_turn(session, 20)
    before = _history_tokens(session)

    event = await session.compact_now()

    assert event is not None
    assert event.shrunk_tool_results == 20 - 3
    assert event.dropped_turns == 0
    assert event.tokens_before == before
    assert _history_tokens(session) < before / 3
    assert provider.calls == []
    await session.aclose()


@pytest.mark.asyncio
async def test_over_budget_notice_is_reported_once_per_turn(
    monkeypatch, aida_home: Path, records_home: Path
):
    provider = MockProvider([MockTurn(text="ok")])
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # clamped up to 8000 tokens
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(settings, "mock-profile")

    events = [e async for e in session.send("x" * 60_000)]

    notices = [e for e in events if isinstance(e, ContextTrimmed)]
    assert len(notices) == 1
    assert notices[0].over_budget is True
    assert notices[0].dropped_turns == 0
    assert notices[0].budget_tokens == session._history_budget()
    assert await session._trim_context() is None  # already reported this turn
    await session.aclose()


@pytest.mark.asyncio
async def test_a_tool_result_over_the_cap_is_saved_to_scratch(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    from aida.core.events import ToolCallFinished
    from aida.core.tools import NativeTool, ToolResult
    from aida.providers.base import ToolSchema
    from aida.providers.mock import MockToolCall

    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # 8000-token budget -> 8000-char cap
    settings.app.scratch_dir = str(tmp_path / "scratch")
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [MockTurn(tool_calls=[MockToolCall(name="big", id="call_9")]), MockTurn(text="done")]
        ),
    )
    full = "HEAD" + "q" * 50_000 + "TAIL"

    async def _big(_args):
        return ToolResult(content=full)

    tools = {
        "big": NativeTool(
            schema=ToolSchema(name="big", description="", parameters={"type": "object"}),
            func=_big,
        )
    }
    session = ChatSession(settings, "mock-profile", tools=tools)

    events = [e async for e in session.send("go")]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.result == full  # the frontend still gets everything
    tool_message = next(m for m in session.messages if m.role == "tool")
    assert len(tool_message.content) < 9_000
    assert tool_message.content.startswith("HEAD") and tool_message.content.endswith("TAIL")
    [saved] = list((tmp_path / "scratch" / "tool-results").iterdir())
    assert "big-call_9" in saved.name
    assert saved.read_text(encoding="utf-8") == full
    assert str(saved) in tool_message.content
    await session.aclose()


@pytest.mark.asyncio
async def test_mid_turn_shrinking_keeps_full_results_in_the_database(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """Stage A changes only what is sent; the record keeps every result."""
    from aida.artifacts.store import ArtifactStore
    from aida.core.context import is_shrunk_tool_result, repair_tool_call_pairing
    from aida.core.tools import NativeTool, ToolResult
    from aida.persistence.recorder import ConversationRecorder
    from aida.persistence.store import ConversationStore
    from aida.providers.base import ToolSchema
    from aida.providers.mock import MockToolCall

    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # 8000-token budget
    rounds = 10
    script = [
        MockTurn(tool_calls=[MockToolCall(name="read", id=f"call_{i}")]) for i in range(rounds)
    ]
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: SummarizingMockProvider([*script, MockTurn(text="done")]),
    )
    counter = iter(range(rounds))

    async def _read(_args):
        return ToolResult(content=f"result {next(counter)} " + "r" * 7_000)

    tools = {
        "read": NativeTool(
            schema=ToolSchema(name="read", description="", parameters={"type": "object"}),
            func=_read,
        )
    }
    recorder = ConversationRecorder(
        ConversationStore(tmp_path / "aida.db"),
        ArtifactStore(base_dir=tmp_path / "artifacts"),
        tmp_path / "records",
    )
    session = ChatSession(settings, "mock-profile", tools=tools, recorder=recorder)

    events = [e async for e in session.send("read everything")]

    assert any(isinstance(e, ContextTrimmed) and e.shrunk_tool_results for e in events)
    assert any(is_shrunk_tool_result(m) for m in session.messages)
    assert repair_tool_call_pairing(session.messages) == session.messages
    stored_tools = [m for m in recorder.load_history() if m.role == "tool"]
    assert len(stored_tools) == rounds
    assert not any(is_shrunk_tool_result(m) for m in stored_tools)
    assert session.messages[-1].content == "done"
    await session.aclose()


@pytest.mark.asyncio
async def test_trimming_never_drops_the_request_that_started_the_running_turn(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Retrieved passages arrive as a role="user" message, which starts a
    turn of its own; lowering the floor must still keep the user's request."""
    provider = SummarizingMockProvider([])
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 2000  # clamped up to 8000 tokens
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(settings, "mock-profile")
    _add_old_turns(session, 2)
    request = Message(role="user", content="the request " + "x" * 30_000)
    session.messages.append(request)
    session.messages.append(Message(role="user", content="retrieved passages " + "p" * 10_000))
    session._turn_user_message = request
    assert session._current_turn_floor() == 2

    event = await session._trim_context()

    assert event is not None and event.dropped_turns == 2
    assert any(m is request for m in session.messages)
    await session.aclose()


@pytest.mark.asyncio
async def test_a_web_search_result_gets_the_lower_per_tool_cap(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """At a 1M window the general cap is ~38k tokens; a search result is
    held to 10,000 characters, with the rest saved to scratch."""
    from aida.core.tools import NativeTool, ToolResult
    from aida.providers.base import ToolSchema
    from aida.providers.mock import MockToolCall

    name = "parallel-websearch__web_search"
    settings = _settings_with_profile(context_window=1_000_000)
    settings.app.scratch_dir = str(tmp_path / "scratch")
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider(
            [MockTurn(tool_calls=[MockToolCall(name=name, id="s1")]), MockTurn(text="done")]
        ),
    )
    full = "HIT1 " + "w" * 28_000 + " LAST"

    async def _search(_args):
        return ToolResult(content=full)

    tools = {
        name: NativeTool(
            schema=ToolSchema(name=name, description="", parameters={"type": "object"}),
            func=_search,
        )
    }
    session = ChatSession(settings, "mock-profile", tools=tools)
    _ = [e async for e in session.send("search")]

    tool_message = next(m for m in session.messages if m.role == "tool")
    assert len(tool_message.content) < 10_500
    assert tool_message.content.startswith("HIT1") and tool_message.content.endswith("LAST")
    [saved] = list((tmp_path / "scratch" / "tool-results").iterdir())
    assert saved.read_text(encoding="utf-8") == full
    await session.aclose()
