from __future__ import annotations

from pathlib import Path

import pytest

from aida.cli.chat import (
    ChatSession,
    UnknownMcpServerError,
    UnknownProfileError,
    _build_parser,
    cli_confirm,
    print_event,
    resolve_mcp_servers,
    resolve_profile,
)
from aida.config.settings import (
    McpConfig,
    McpServerConfig,
    ProviderProfile,
    Settings,
    load_settings,
)
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
from aida.workspace.safety import ConfirmationRequest


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


# --- resolve_mcp_servers / CLI flags -----------------------------------------


def _mcp_config() -> McpConfig:
    return McpConfig(
        servers={
            "pyirena": McpServerConfig(name="pyirena", command="pyirena-mcp", groups=["analysis"]),
            "bait": McpServerConfig(name="bait", command="bait-mcp", groups=["analysis"]),
        }
    )


def test_resolve_mcp_servers_defaults_to_none():
    assert resolve_mcp_servers(_mcp_config(), group="", names=[]) == []


def test_resolve_mcp_servers_by_group():
    servers = resolve_mcp_servers(_mcp_config(), group="analysis", names=[])
    assert {s.name for s in servers} == {"pyirena", "bait"}


def test_resolve_mcp_servers_explicit_list():
    servers = resolve_mcp_servers(_mcp_config(), group="", names=["pyirena"])
    assert {s.name for s in servers} == {"pyirena"}


def test_resolve_mcp_servers_explicit_wins_over_group():
    servers = resolve_mcp_servers(_mcp_config(), group="analysis", names=["pyirena"])
    assert {s.name for s in servers} == {"pyirena"}


def test_resolve_mcp_servers_unknown_name_raises():
    with pytest.raises(UnknownMcpServerError):
        resolve_mcp_servers(_mcp_config(), group="", names=["typo"])


def test_parser_accepts_mcp_flags():
    args = _build_parser().parse_args(
        ["--profile", "p", "--mcp-group", "analysis", "--mcp", "pyirena,bait"]
    )
    assert args.mcp_group == "analysis"
    assert args.mcp == "pyirena,bait"


def test_parser_mcp_flags_default_empty():
    args = _build_parser().parse_args(["--profile", "p"])
    assert args.mcp_group == ""
    assert args.mcp == ""


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


# --- cli_confirm (Phase 6 default ConfirmCallback) --------------------------


@pytest.mark.asyncio
async def test_cli_confirm_yes_approves(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    request = ConfirmationRequest(action="write", path="/tmp/x", detail="Write /tmp/x?")
    assert await cli_confirm(request) is True


@pytest.mark.asyncio
async def test_cli_confirm_blank_denies(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    request = ConfirmationRequest(action="delete", path="/tmp/x", detail="Delete /tmp/x?")
    assert await cli_confirm(request) is False


@pytest.mark.asyncio
async def test_cli_confirm_shows_the_request_detail(monkeypatch):
    seen_prompts = []

    def _fake_input(prompt):
        seen_prompts.append(prompt)
        return "no"

    monkeypatch.setattr("builtins.input", _fake_input)
    request = ConfirmationRequest(action="write", path="/tmp/x", detail="Write outside allowed folders: /tmp/x")
    assert await cli_confirm(request) is False
    assert "Write outside allowed folders: /tmp/x" in seen_prompts[0]


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
