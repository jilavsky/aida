"""Conversations naming themselves, end to end through ``ChatSession``.

The companion to ``tests/test_titling.py`` (which covers the prompt and
the answer-cleaning in isolation): this is about *when* the extra provider
call happens, when it must not, and that nothing it can do ever costs the
user a turn.

``MockProvider`` replays one scripted ``MockTurn`` per ``complete()`` call,
and a titling check is a ``complete()`` call of its own — so a test that
expects one reply *and* one title has to script two turns. Running out of
script yields an ``AgentError``, which is exactly the "titling failed"
path, so a short script is also how a failure is simulated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.store import ArtifactStore
from aida.config.settings import ProviderProfile, Settings, load_settings
from aida.core.events import ConversationTitled
from aida.core.session import ChatSession
from aida.persistence.recorder import ConversationRecorder
from aida.persistence.store import (
    TITLE_DERIVED,
    TITLE_GENERATED,
    TITLE_MANUAL,
    ConversationStore,
)
from aida.providers.mock import MockProvider, MockTurn


def _settings(**app_overrides) -> Settings:
    settings = load_settings()
    settings.providers.profiles["mock-profile"] = ProviderProfile(
        name="mock-profile", kind="openai_compat", model="mock-model"
    )
    for key, value in app_overrides.items():
        setattr(settings.app, key, value)
    return settings


def _recorder(tmp_path: Path) -> ConversationRecorder:
    return ConversationRecorder(
        ConversationStore(tmp_path / "aida.db"),
        ArtifactStore(base_dir=tmp_path / "artifacts"),
        tmp_path / "records",
    )


def _session(monkeypatch, tmp_path: Path, script: list[MockTurn], **app_overrides) -> ChatSession:
    provider = MockProvider(script)
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    return ChatSession(_settings(**app_overrides), "mock-profile", recorder=_recorder(tmp_path))


async def _turn(session: ChatSession, text: str) -> list:
    return [event async for event in session.send(text)]


def _stored_title(session: ChatSession) -> str | None:
    """Read the title back out of the database rather than off the
    recorder, so the test proves the rename was actually persisted."""
    summary = session.recorder.store.get_conversation(session.recorder.conversation_id)
    return None if summary is None else summary.title


# --- the first title -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_reply_replaces_the_derived_placeholder(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    session = _session(
        monkeypatch,
        tmp_path,
        [MockTurn(text="load the file first"), MockTurn(text="Unified fit of S12_0042.h5")],
    )
    try:
        events = await _turn(session, "can you have a look at the file I just put in the target")
        titled = [e for e in events if isinstance(e, ConversationTitled)]
        assert len(titled) == 1
        assert titled[0].title == "Unified fit of S12_0042.h5"
        assert titled[0].conversation_id == session.recorder.conversation_id
        assert _stored_title(session) == "Unified fit of S12_0042.h5"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_the_event_comes_after_the_reply_not_before_it(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """A title is a postscript to the turn, never something that delays
    the answer appearing — see ChatSession.send, which runs the check
    after the mutation lock is released."""
    session = _session(
        monkeypatch, tmp_path, [MockTurn(text="load the file first"), MockTurn(text="Unified fit")]
    )
    try:
        events = await _turn(session, "have a look")
        assert isinstance(events[-1], ConversationTitled)
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_the_setting_turns_the_whole_thing_off(monkeypatch, aida_home: Path, tmp_path: Path):
    session = _session(
        monkeypatch,
        tmp_path,
        [MockTurn(text="load the file first"), MockTurn(text="Unified fit")],
        auto_title_conversations=False,
    )
    try:
        events = await _turn(session, "have a look at run_042")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        # Not even a request was made: the second scripted turn is untouched.
        assert len(session.provider.calls) == 1
        assert _stored_title(session) == "have a look at run_042"  # the placeholder
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_session_with_no_recorder_never_tries(monkeypatch, aida_home: Path, tmp_path: Path):
    provider = MockProvider([MockTurn(text="sure")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(_settings(), "mock-profile")  # no recorder
    try:
        events = await _turn(session, "hello")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        assert len(provider.calls) == 1
    finally:
        await session.aclose()


# --- the cadence -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_existing_title_is_not_rechecked_every_turn(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """Turn 1 names it; turns 2-5 cost nothing extra; turn 6 re-checks."""
    script = [MockTurn(text=f"reply {i}") for i in range(20)]
    session = _session(monkeypatch, tmp_path, script, auto_title_interval_turns=5)
    try:
        await _turn(session, "q1")
        after_first = len(session.provider.calls)
        assert after_first == 2  # the reply, then the title

        for index in range(2, 6):
            await _turn(session, f"q{index}")
        # Four more turns, four more provider calls — no title checks.
        assert len(session.provider.calls) == after_first + 4

        await _turn(session, "q6")
        assert len(session.provider.calls) == after_first + 4 + 2  # reply + re-check
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_keep_leaves_the_title_alone_and_emits_nothing(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    session = _session(
        monkeypatch,
        tmp_path,
        [
            MockTurn(text="reply one"),
            MockTurn(text="Unified fit of S12_0042.h5"),
            MockTurn(text="reply two"),
            MockTurn(text="KEEP"),
        ],
        auto_title_interval_turns=1,
    )
    try:
        await _turn(session, "q1")
        events = await _turn(session, "q2")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        assert _stored_title(session) == "Unified fit of S12_0042.h5"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_changed_subject_gets_a_new_title(monkeypatch, aida_home: Path, tmp_path: Path):
    session = _session(
        monkeypatch,
        tmp_path,
        [
            MockTurn(text="reply one"),
            MockTurn(text="Unified fit of S12_0042.h5"),
            MockTurn(text="reply two"),
            MockTurn(text="EPICS motor scan setup"),
        ],
        auto_title_interval_turns=1,
    )
    try:
        await _turn(session, "q1")
        events = await _turn(session, "actually, let's set up the motor scan")
        titled = [e for e in events if isinstance(e, ConversationTitled)]
        assert [e.title for e in titled] == ["EPICS motor scan setup"]
        assert _stored_title(session) == "EPICS motor scan setup"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_recheck_is_told_the_current_title(monkeypatch, aida_home: Path, tmp_path: Path):
    session = _session(
        monkeypatch,
        tmp_path,
        [
            MockTurn(text="reply one"),
            MockTurn(text="Unified fit of S12_0042.h5"),
            MockTurn(text="reply two"),
            MockTurn(text="KEEP"),
        ],
        auto_title_interval_turns=1,
    )
    try:
        await _turn(session, "q1")
        first_request = session.provider.calls[1][0][0].content
        assert "KEEP" not in first_request  # nothing to keep yet

        await _turn(session, "q2")
        recheck_request = session.provider.calls[3][0][0].content
        assert "Unified fit of S12_0042.h5" in recheck_request
        assert "KEEP" in recheck_request
    finally:
        await session.aclose()


# --- a name a person typed is never touched --------------------------------


@pytest.mark.asyncio
async def test_a_manually_renamed_conversation_is_never_retitled(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    session = _session(
        monkeypatch, tmp_path, [MockTurn(text="reply one"), MockTurn(text="Some Other Name")]
    )
    try:
        session.recorder.set_title("USAXS beamtime notes", source=TITLE_MANUAL)
        events = await _turn(session, "q1")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        assert len(session.provider.calls) == 1  # no titling request at all
        assert _stored_title(session) == "USAXS beamtime notes"
    finally:
        await session.aclose()


def _resume(tmp_path: Path, conv_id: str) -> ConversationRecorder:
    return ConversationRecorder(
        ConversationStore(tmp_path / "aida.db"),
        ArtifactStore(base_dir=tmp_path / "artifacts"),
        tmp_path / "records",
        conversation_id=conv_id,
        resume=True,
    )


@pytest.mark.asyncio
async def test_the_lock_survives_a_resume(monkeypatch, aida_home: Path, tmp_path: Path):
    """The whole point of persisting where a name came from: relaunching
    must not hand the model permission to rename something the user
    named."""
    recorder = _recorder(tmp_path)
    conv_id = recorder.conversation_id
    recorder.set_title("USAXS beamtime notes", source=TITLE_MANUAL)
    recorder.store.close()

    resumed = _resume(tmp_path, conv_id)
    assert resumed.title_locked is True
    assert resumed.title == "USAXS beamtime notes"
    resumed.store.close()


# --- resuming must not churn the name --------------------------------------


@pytest.mark.asyncio
async def test_resuming_a_named_conversation_does_not_rename_it_on_the_next_turn(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """A generated name has to be stable across a resume. Before
    title_source was persisted, a resumed session had no idea the
    conversation was already named, so the first turn after every resume
    re-titled it unconditionally — with no chance to answer KEEP."""
    recorder = _recorder(tmp_path)
    conv_id = recorder.conversation_id
    recorder.set_title("Unified fit of S12_0042.h5", source=TITLE_GENERATED)
    recorder.store.close()

    provider = MockProvider([MockTurn(text="reply"), MockTurn(text="KEEP")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(
        _settings(auto_title_interval_turns=1), "mock-profile", recorder=_resume(tmp_path, conv_id)
    )
    try:
        events = await _turn(session, "and the limits?")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        # It was asked, and asked properly: the current name was offered
        # back with KEEP available.
        recheck = provider.calls[1][0][0].content
        assert "Unified fit of S12_0042.h5" in recheck
        assert "KEEP" in recheck
        assert _stored_title(session) == "Unified fit of S12_0042.h5"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_resuming_a_conversation_that_only_has_a_placeholder_still_names_it(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """The other half of the same rule, and the upgrade path for every
    conversation that predates this feature: a derived placeholder is
    never offered for KEEP, so it cannot survive as the name."""
    recorder = _recorder(tmp_path)
    conv_id = recorder.conversation_id
    recorder.set_title("can you look at the file I just", source=TITLE_DERIVED)
    recorder.store.close()

    provider = MockProvider([MockTurn(text="reply"), MockTurn(text="Unified fit of S12_0042.h5")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    session = ChatSession(_settings(), "mock-profile", recorder=_resume(tmp_path, conv_id))
    try:
        events = await _turn(session, "carry on")
        assert [e.title for e in events if isinstance(e, ConversationTitled)] == [
            "Unified fit of S12_0042.h5"
        ]
        assert "KEEP" not in provider.calls[1][0][0].content
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_kept_placeholder_is_not_promoted_to_a_generated_name(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """If a titling call comes back with something unusable, the
    conversation must stay 'derived' — recording it as named would
    freeze the placeholder in place forever."""
    session = _session(monkeypatch, tmp_path, [MockTurn(text="reply"), MockTurn(text="  ")])
    try:
        await _turn(session, "q1")
        assert session.recorder.title_source == TITLE_DERIVED
    finally:
        await session.aclose()


# --- failure is never the user's problem -----------------------------------


@pytest.mark.asyncio
async def test_a_failed_titling_call_leaves_the_turn_intact(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """Only one turn scripted, so the titling call runs the MockProvider
    out of script and gets an AgentError back."""
    session = _session(monkeypatch, tmp_path, [MockTurn(text="here is your answer")])
    try:
        events = await _turn(session, "q1")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        # The turn itself completed and was recorded, untouched.
        assert [m.role for m in session.messages[-2:]] == ["user", "assistant"]
        assert session.messages[-1].content == "here is your answer"
        assert _stored_title(session) == "q1"  # still the derived placeholder
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_raising_provider_does_not_escape_the_turn(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    session = _session(
        monkeypatch, tmp_path, [MockTurn(text="here is your answer"), MockTurn(text="A Title")]
    )
    real_complete = session.provider.complete

    def _explode_on_the_second_call(messages, tools, settings):
        if len(session.provider.calls) >= 1:
            raise RuntimeError("endpoint went away")
        return real_complete(messages, tools, settings)

    try:
        session.provider.complete = _explode_on_the_second_call
        events = await _turn(session, "q1")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        assert session.messages[-1].content == "here is your answer"
    finally:
        session.provider.complete = real_complete
        await session.aclose()


@pytest.mark.asyncio
async def test_an_empty_or_unusable_answer_changes_nothing(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    session = _session(monkeypatch, tmp_path, [MockTurn(text="reply"), MockTurn(text="   ")])
    try:
        events = await _turn(session, "q1")
        assert not any(isinstance(e, ConversationTitled) for e in events)
        assert _stored_title(session) == "q1"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_title_identical_to_the_current_one_emits_nothing(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """Nothing changed for the user, so there is nothing to tell them —
    the event means "the name in front of you just changed"."""
    session = _session(monkeypatch, tmp_path, [MockTurn(text="reply"), MockTurn(text="q1")])
    try:
        events = await _turn(session, "q1")
        assert not any(isinstance(e, ConversationTitled) for e in events)
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_the_titling_request_carries_no_tools(monkeypatch, aida_home: Path, tmp_path: Path):
    """A summarizer must never try to call a tool — the same rule
    _compact_context follows."""
    session = _session(monkeypatch, tmp_path, [MockTurn(text="reply"), MockTurn(text="A Title")])
    try:
        await _turn(session, "q1")
        _messages, tools, settings = session.provider.calls[1]
        assert tools == []
        assert settings.supports_vision is False
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_a_persistently_failing_endpoint_is_not_retried_every_turn(
    monkeypatch, aida_home: Path, tmp_path: Path
):
    """A conversation stuck on its placeholder keeps trying, but on the
    ordinary cadence — not on every single turn, which would double the
    cost of the whole session against an endpoint that is never going to
    answer."""
    # Every reply is unusable, so the title never takes.
    script = [MockTurn(text="   ") for _ in range(40)]
    session = _session(monkeypatch, tmp_path, script, auto_title_interval_turns=5)
    try:
        await _turn(session, "q1")
        assert len(session.provider.calls) == 2  # the turn, and one attempt

        for index in range(2, 6):
            await _turn(session, f"q{index}")
        assert len(session.provider.calls) == 2 + 4  # four turns, no retries

        await _turn(session, "q6")
        assert len(session.provider.calls) == 2 + 4 + 2  # the interval came round
        assert session.recorder.title_source == TITLE_DERIVED
    finally:
        await session.aclose()
