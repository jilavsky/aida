from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.chat import ChatSession, UnknownProfileError, print_event, resolve_profile
from aida.config.settings import ProviderProfile, Settings, load_settings
from aida.core.events import (
    AgentError,
    FileArtifactCreated,
    ImageArtifactCreated,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
)
from aida.providers.mock import MockProvider, MockToolCall, MockTurn


def _settings_with_profile(name="mock-profile", **overrides) -> Settings:
    settings = load_settings()
    settings.providers.profiles[name] = ProviderProfile(
        name=name, kind="openai_compat", model="mock-model", **overrides
    )
    return settings


def test_resolve_profile_found(aida_home: Path, records_home: Path):
    settings = _settings_with_profile()
    profile = resolve_profile(settings, "mock-profile")
    assert profile.model == "mock-model"


def test_resolve_profile_missing_lists_available(aida_home: Path, records_home: Path):
    settings = _settings_with_profile()
    with pytest.raises(UnknownProfileError) as exc_info:
        resolve_profile(settings, "does-not-exist")
    assert "mock-profile" in str(exc_info.value)


# --- print_event formatting -------------------------------------------------


def test_print_event_text_delta(capsys):
    print_event(TextStarted(message_id="m1"))
    print_event(TextDelta(message_id="m1", text="hello"))
    out = capsys.readouterr().out
    assert out == "hello"


def test_print_event_text_finished_adds_newline(capsys):
    print_event(TextFinished(message_id="m1", text="hello"))
    assert capsys.readouterr().out == "\n"


def test_print_event_tool_call_started(capsys):
    print_event(ToolCallStarted(call_id="c1", tool_name="get_time", arguments={"tz": "utc"}))
    out = capsys.readouterr().out
    assert "get_time" in out
    assert "tz" in out


def test_print_event_tool_call_finished_error(capsys):
    print_event(ToolCallFinished(call_id="c1", tool_name="get_time", result="boom", is_error=True))
    out = capsys.readouterr().out
    assert "error" in out
    assert "boom" in out


def test_print_event_image_artifact(capsys):
    print_event(ImageArtifactCreated(artifact_id="a1", call_id="c1", mime_type="image/png", path="/tmp/x.png"))
    assert "/tmp/x.png" in capsys.readouterr().out


def test_print_event_file_artifact(capsys):
    print_event(FileArtifactCreated(artifact_id="a1", call_id="c1", path="/tmp/x.md"))
    assert "/tmp/x.md" in capsys.readouterr().out


def test_print_event_agent_error(capsys):
    print_event(AgentError(layer="provider", message="boom", detail="net down"))
    out = capsys.readouterr().out
    assert "provider" in out
    assert "boom" in out
    assert "net down" in out


# --- ChatSession -------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_session_send_streams_and_updates_history(monkeypatch, aida_home: Path, records_home: Path):
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="hi there")])
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")
    events = [e async for e in session.send("hello")]

    assert any(isinstance(e, TextFinished) for e in events)
    assert session.messages[-2].role == "user"
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "hi there"


@pytest.mark.asyncio
async def test_chat_session_profile_switch_preserves_history(monkeypatch, aida_home: Path, records_home: Path):
    settings = _settings_with_profile("profile-a")
    settings.providers.profiles["profile-b"] = ProviderProfile(
        name="profile-b", kind="openai_compat", model="model-b"
    )

    provider_a = MockProvider([MockTurn(text="from a")])
    provider_b = MockProvider([MockTurn(text="from b")])
    providers = {"profile-a": provider_a, "profile-b": provider_b}
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: providers[profile.name])

    session = ChatSession(settings, "profile-a")
    _ = [e async for e in session.send("first")]
    assert session.profile_name == "profile-a"

    await session.switch_profile("profile-b")
    assert session.profile_name == "profile-b"
    # History from before the switch is still there.
    assert any(m.content == "first" for m in session.messages)

    _ = [e async for e in session.send("second")]
    assert session.messages[-1].content == "from b"
    # Both turns' history preserved across the switch.
    roles_and_content = [(m.role, m.content) for m in session.messages]
    assert ("user", "first") in roles_and_content
    assert ("user", "second") in roles_and_content


@pytest.mark.asyncio
async def test_chat_session_switch_profile_closes_old_provider(monkeypatch, aida_home: Path, records_home: Path):
    """Regression test: switching profiles (and ending the session) must
    close the outgoing provider's connections rather than leaking them —
    see test_provider_lifecycle.py for why this matters."""
    settings = _settings_with_profile("profile-a")
    settings.providers.profiles["profile-b"] = ProviderProfile(
        name="profile-b", kind="openai_compat", model="model-b"
    )

    closed = []

    class _TrackingProvider(MockProvider):
        def __init__(self, tag, *a, **kw):
            super().__init__(*a, **kw)
            self.tag = tag

        async def aclose(self):
            closed.append(self.tag)

    provider_a = _TrackingProvider("a", [MockTurn(text="from a")])
    provider_b = _TrackingProvider("b", [MockTurn(text="from b")])
    providers = {"profile-a": provider_a, "profile-b": provider_b}
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: providers[profile.name])

    session = ChatSession(settings, "profile-a")
    await session.switch_profile("profile-b")
    assert closed == ["a"]  # old provider closed, new one left open

    await session.aclose()
    assert closed == ["a", "b"]  # session end closes whatever is current


def test_chat_session_unknown_profile_raises(aida_home: Path, records_home: Path):
    settings = _settings_with_profile()
    with pytest.raises(UnknownProfileError):
        ChatSession(settings, "nope")


@pytest.mark.asyncio
async def test_chat_session_loads_skills_into_system_message(monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path):
    from aida.config import paths as paths_module

    skills_dir = aida_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "saxs-basics.md").write_text("SAXS is small-angle X-ray scattering.", encoding="utf-8")
    monkeypatch.setattr(paths_module, "skills_dir", lambda: skills_dir)
    monkeypatch.setattr("aida.cli.chat.skills_dir", lambda: skills_dir)

    settings = _settings_with_profile()
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))

    session = ChatSession(settings, "mock-profile", skill_names=["saxs-basics"])
    assert session.messages
    assert session.messages[0].role == "system"
    assert "small-angle X-ray scattering" in session.messages[0].content


@pytest.mark.asyncio
async def test_chat_session_tool_round_trip_with_default_tools(monkeypatch, aida_home: Path, records_home: Path):
    provider = MockProvider(
        [
            MockTurn(tool_calls=[MockToolCall(name="get_current_time", id="call_1")]),
            MockTurn(text="the time is now"),
        ]
    )
    monkeypatch.setattr("aida.cli.chat.build_provider", lambda profile: provider)
    settings = _settings_with_profile()

    session = ChatSession(settings, "mock-profile")
    events = [e async for e in session.send("what time is it?")]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.tool_name == "get_current_time"
    assert "utc_iso" in finished.result
