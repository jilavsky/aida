from __future__ import annotations

from pathlib import Path

import pytest

from aida.artifacts.base import FileArtifact, ImageArtifact
from aida.artifacts.store import ArtifactStore
from aida.cli.chat import (
    ChatSession,
    UnknownMcpServerError,
    UnknownProfileError,
    _build_parser,
    _repl_loop,
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
    ContextTrimmed,
    FileArtifactCreated,
    ImageArtifactCreated,
    RetrievalPerformed,
    TextDelta,
    TextFinished,
    TextStarted,
    ToolCallFinished,
    ToolCallStarted,
    UsageInfo,
)
from aida.core.tools import NativeTool, ToolResult
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.chunking import Chunk
from aida.knowledge.rag.retrieval import ActiveKnowledgeBase
from aida.persistence.recorder import ConversationRecorder
from aida.persistence.store import ConversationStore
from aida.providers.base import Message, ToolSchema
from aida.providers.mock import MockProvider, MockToolCall, MockTurn
from aida.providers.mock_embeddings import MockEmbeddings
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


def test_parser_max_iterations_defaults_to_none():
    args = _build_parser().parse_args(["--profile", "p"])
    assert args.max_iterations is None


def test_parser_max_iterations_accepts_an_override():
    args = _build_parser().parse_args(["--profile", "p", "--max-iterations", "500"])
    assert args.max_iterations == 500


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


def test_print_event_context_trimmed(capsys):
    print_event(ContextTrimmed(dropped_turns=3, estimated_tokens=512))
    out = capsys.readouterr().out
    assert "3" in out
    assert "512" in out
    assert "context" in out.lower()


def test_print_event_context_trimmed_singular_turn_wording(capsys):
    print_event(ContextTrimmed(dropped_turns=1, estimated_tokens=100))
    out = capsys.readouterr().out
    assert "1 old turn " in out  # not "1 old turns"


def test_print_event_agent_error(capsys):
    print_event(AgentError(layer="provider", message="boom", detail="net down"))
    out = capsys.readouterr().out
    assert "provider" in out
    assert "boom" in out
    assert "net down" in out


def test_print_event_usage_info_with_duration_shows_tokens_per_second(capsys):
    print_event(UsageInfo(input_tokens=100, output_tokens=50, duration_seconds=2.0))
    out = capsys.readouterr().out
    assert "100" in out
    assert "50" in out
    assert "25.0 tok/s" in out


def test_print_event_usage_info_without_duration_still_shows_tokens(capsys):
    print_event(UsageInfo(input_tokens=100, output_tokens=50))
    out = capsys.readouterr().out
    assert "100" in out
    assert "50" in out


def test_print_event_usage_info_with_no_tokens_prints_nothing(capsys):
    print_event(UsageInfo())
    assert capsys.readouterr().out == ""


def test_print_event_usage_info_shows_cache_read_tokens_when_present(capsys):
    """B3: prompt-caching savings must actually be visible, not just an
    invisible backend detail — the CLI usage line grows a ", N cached"
    suffix whenever a provider reports cache_read_input_tokens."""
    print_event(UsageInfo(input_tokens=100, output_tokens=50, cache_read_input_tokens=80))
    out = capsys.readouterr().out
    assert "80 cached" in out


def test_print_event_usage_info_omits_cache_note_when_zero(capsys):
    print_event(UsageInfo(input_tokens=100, output_tokens=50))
    out = capsys.readouterr().out
    assert "cached" not in out


def test_print_event_usage_info_with_duration_shows_cache_note_too(capsys):
    print_event(UsageInfo(input_tokens=100, output_tokens=50, duration_seconds=2.0, cache_read_input_tokens=30))
    out = capsys.readouterr().out
    assert "30 cached" in out
    assert "25.0 tok/s" in out


# --- _completion_settings_for_profile (B2) -----------------------------------


def test_completion_settings_for_profile_sends_no_temperature_when_unset(aida_home: Path, records_home: Path):
    """A profile that never set a temperature must not have one invented
    for it — AIDA used to substitute 0.7, which models that fix temperature
    at their own default then reject outright."""
    from aida.core.session import _completion_settings_for_profile

    settings = _settings_with_profile()
    profile = settings.providers.profiles["mock-profile"]

    completion_settings = _completion_settings_for_profile(profile)

    assert completion_settings.model == "mock-model"
    assert completion_settings.temperature is None  # omitted from the request entirely
    assert completion_settings.max_tokens is None
    assert completion_settings.supports_vision is False


def test_completion_settings_for_profile_uses_profile_overrides(aida_home: Path, records_home: Path):
    from aida.core.session import _completion_settings_for_profile

    settings = _settings_with_profile(temperature=0.2, max_tokens=512, supports_vision=True)
    profile = settings.providers.profiles["mock-profile"]

    completion_settings = _completion_settings_for_profile(profile)

    assert completion_settings.temperature == 0.2
    assert completion_settings.max_tokens == 512
    assert completion_settings.supports_vision is True


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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")
    events = [e async for e in session.send("hello")]

    assert any(isinstance(e, TextFinished) for e in events)
    assert session.messages[-2].role == "user"
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "hi there"


@pytest.mark.asyncio
async def test_chat_session_send_attaches_images_to_the_user_message(
    monkeypatch, aida_home: Path, records_home: Path
):
    """B1: ChatSession.send's optional images kwarg lands on the outgoing
    user Message's .images, not folded into .content — so a vision-capable
    profile's translation layer can pick them up (see
    test_provider_translation.py) while a non-vision profile just ignores
    them, exactly as before B1 existed."""
    from aida.providers.base import ImageRef

    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="looks like a plot")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")
    refs = [ImageRef(path="/tmp/plot.png", mime_type="image/png")]
    events = [e async for e in session.send("what is this?", images=refs)]

    assert any(isinstance(e, TextFinished) for e in events)
    user_message = next(m for m in session.messages if m.role == "user" and m.content == "what is this?")
    assert user_message.images == refs


@pytest.mark.asyncio
async def test_chat_session_send_with_no_images_defaults_to_empty_list(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="hi there")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")
    _ = [e async for e in session.send("hello")]

    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.images == []


@pytest.mark.asyncio
async def test_chat_session_profile_switch_preserves_history(monkeypatch, aida_home: Path, records_home: Path):
    settings = _settings_with_profile("profile-a")
    settings.providers.profiles["profile-b"] = ProviderProfile(
        name="profile-b", kind="openai_compat", model="model-b"
    )

    provider_a = MockProvider([MockTurn(text="from a")])
    provider_b = MockProvider([MockTurn(text="from b")])
    providers = {"profile-a": provider_a, "profile-b": provider_b}
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: providers[profile.name])

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: providers[profile.name])

    session = ChatSession(settings, "profile-a")
    await session.switch_profile("profile-b")
    assert closed == ["a"]  # old provider closed, new one left open

    await session.aclose()
    assert closed == ["a", "b"]  # session end closes whatever is current


def test_chat_session_uses_configured_max_iterations(monkeypatch, aida_home: Path, records_home: Path):
    """Bug report: iteration cap was hardcoded at 10 with no way to raise
    it. AppConfig.max_agent_iterations must actually reach the AgentLoop."""
    settings = _settings_with_profile()
    settings.app.max_agent_iterations = 250
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session = ChatSession(settings, "mock-profile")
    assert session.loop.max_iterations == 250


@pytest.mark.asyncio
async def test_chat_session_switch_profile_keeps_configured_max_iterations(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile("profile-a")
    settings.app.max_agent_iterations = 42
    settings.providers.profiles["profile-b"] = ProviderProfile(
        name="profile-b", kind="openai_compat", model="model-b"
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))

    session = ChatSession(settings, "profile-a")
    await session.switch_profile("profile-b")
    assert session.loop.max_iterations == 42


@pytest.mark.asyncio
async def test_chat_session_accumulates_usage_across_turns(monkeypatch, aida_home: Path, records_home: Path):
    """Bug report: "Can we get cost estimate... token use may be
    better... at this moment it is a black box." ChatSession.send() must
    accumulate UsageInfo tokens across the whole session, not just report
    per-turn."""
    settings = _settings_with_profile()
    provider = MockProvider(
        [
            MockTurn(text="first", input_tokens=100, output_tokens=20),
            MockTurn(text="second", input_tokens=50, output_tokens=10),
        ]
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")
    assert session.total_input_tokens == 0
    assert session.total_output_tokens == 0

    _ = [e async for e in session.send("one")]
    assert session.total_input_tokens == 100
    assert session.total_output_tokens == 20

    _ = [e async for e in session.send("two")]
    assert session.total_input_tokens == 150
    assert session.total_output_tokens == 30


@pytest.mark.asyncio
async def test_repl_max_iterations_command_raises_the_cap_mid_session(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Bug report: hit the iteration cap mid-session with no way to raise
    it short of quitting and hand-editing AppConfig — the Settings-dialog
    control (GUI) only reaches a *new* AgentLoop, and the CLI has no
    dialog at all. /max-iterations must take effect on session.loop
    immediately, without restarting."""
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    session = ChatSession(settings, "mock-profile")
    assert session.loop.max_iterations == 10

    lines = iter(["/max-iterations 500", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))

    await _repl_loop(session)
    assert session.loop.max_iterations == 500


@pytest.mark.asyncio
async def test_repl_max_iterations_command_rejects_non_numeric_input(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    session = ChatSession(settings, "mock-profile")

    lines = iter(["/max-iterations nope", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))

    await _repl_loop(session)
    assert session.loop.max_iterations == 10  # unchanged
    assert "Not a number" in capsys.readouterr().out


# --- /compact (PLAN.md §1.3 / planning/context_management.md §3.4) --------


@pytest.mark.asyncio
async def test_repl_compact_command_summarizes_and_prints_the_result(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    settings = _settings_with_profile()
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="- did some analysis")]),
    )
    session = ChatSession(settings, "mock-profile")
    for i in range(10):
        session.messages.append(Message(role="user", content=f"old question {i} " + "x" * 2000))
        session.messages.append(Message(role="assistant", content="old answer " + "y" * 2000))

    lines = iter(["/compact", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))

    await _repl_loop(session)
    out = capsys.readouterr().out
    assert "[context] summarized" in out


@pytest.mark.asyncio
async def test_repl_compact_command_reports_nothing_to_compact(
    monkeypatch, aida_home: Path, records_home: Path, capsys
):
    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([]))
    session = ChatSession(settings, "mock-profile")

    lines = iter(["/compact", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(lines))

    await _repl_loop(session)
    assert "nothing to compact yet" in capsys.readouterr().out


def test_print_event_context_trimmed_summarized_wording(capsys):
    print_event(ContextTrimmed(dropped_turns=3, estimated_tokens=512, summarized=True, summary_tokens=80))
    out = capsys.readouterr().out
    assert "summarized 3 old turns" in out
    assert "~80 tokens" in out


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
    monkeypatch.setattr("aida.core.session.skills_dir", lambda: skills_dir)

    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))

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
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings_with_profile()

    session = ChatSession(settings, "mock-profile")
    events = [e async for e in session.send("what time is it?")]

    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.tool_name == "get_current_time"
    assert "utc_iso" in finished.result


# --- U6(b): artifacts are recorded with the owning message's future seq ----


def _recorder(tmp_path: Path) -> ConversationRecorder:
    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(base_dir=tmp_path / "artifacts")
    return ConversationRecorder(store, artifact_store, tmp_path / "records")


@pytest.mark.asyncio
async def test_send_records_image_artifact_with_the_tool_messages_future_seq(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """aida.core.agent.AgentLoop.run yields ImageArtifactCreated/
    FileArtifactCreated *before* appending the tool-result message they
    belong to (see its own comment) — ChatSession must still tag the
    recorded artifact with that message's seq so the GUI resume path
    (aida.ui.qt.chat_panel.load_history) can interleave it back at the
    right position, not just append it at the end like pre-U6(b)."""

    async def _get_plot(_args):
        art = ImageArtifact(data=b"pngbytes", mime_type="image/png", path=str(tmp_path / "plot.png"))
        return ToolResult(content="[image]", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_plot", description="", parameters={"type": "object"}), func=_get_plot
    )
    provider = MockProvider(
        [MockTurn(tool_calls=[MockToolCall(name="get_plot", id="call_1")]), MockTurn(text="here it is")]
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings_with_profile()
    recorder = _recorder(tmp_path)
    session = ChatSession(settings, "mock-profile", tools={"get_plot": tool}, recorder=recorder)

    [e async for e in session.send("plot it")]

    records = recorder.store.load_artifacts(recorder.conversation_id)
    assert len(records) == 1
    tool_message_seq = next(
        seq for seq, m in recorder.store.load_messages_with_seq(recorder.conversation_id) if m.role == "tool"
    )
    assert records[0].seq == tool_message_seq


@pytest.mark.asyncio
async def test_send_records_file_artifact_with_the_tool_messages_future_seq(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    async def _get_report(_args):
        art = FileArtifact(path=str(tmp_path / "report.md"), mime_type="text/markdown")
        return ToolResult(content="[file]", artifacts=[art])

    tool = NativeTool(
        schema=ToolSchema(name="get_report", description="", parameters={"type": "object"}), func=_get_report
    )
    provider = MockProvider(
        [MockTurn(tool_calls=[MockToolCall(name="get_report", id="call_1")]), MockTurn(text="here it is")]
    )
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)
    settings = _settings_with_profile()
    recorder = _recorder(tmp_path)
    session = ChatSession(settings, "mock-profile", tools={"get_report": tool}, recorder=recorder)

    [e async for e in session.send("write a report")]

    records = recorder.store.load_artifacts(recorder.conversation_id)
    assert len(records) == 1
    tool_message_seq = next(
        seq for seq, m in recorder.store.load_messages_with_seq(recorder.conversation_id) if m.role == "tool"
    )
    assert records[0].seq == tool_message_seq


# --- ChatSession.send() retrieval injection (Phase 8 RAG) -------------------


async def _seeded_active_kb(tmp_path: Path, *, name: str = "kb", text: str = "Unified Fit models a SAXS curve.") -> ActiveKnowledgeBase:
    """A ready-to-query ActiveKnowledgeBase backed by a real (tmp-file)
    index with one chunk in it — mirrors test_knowledge_retrieval.py's
    seeding helper, at the granularity ChatSession actually consumes."""
    conn = kb_index.connect(tmp_path / f"{name}.db")
    embedder = MockEmbeddings()
    vector = (await embedder.embed([text]))[0]
    kb_index.replace_file_chunks(
        conn,
        source_path="/docs/fit.md",
        mtime=1.0,
        chunks_with_embeddings=[(Chunk(text=text, heading="Fitting", chunk_index=0), vector)],
        embedding_profile="mock",
    )
    return ActiveKnowledgeBase(name=name, connection=conn, embeddings_provider=embedder, embedding_profile_name="mock")


@pytest.mark.asyncio
async def test_send_injects_retrieved_context_for_a_configured_kb(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="here is the answer")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    kb = await _seeded_active_kb(tmp_path)
    session = ChatSession(settings, "mock-profile", active_knowledge_bases=[kb])
    events = [e async for e in session.send("How does Unified Fit work?")]

    retrieval_events = [e for e in events if isinstance(e, RetrievalPerformed)]
    assert len(retrieval_events) == 1
    assert "kb" in retrieval_events[0].passages_by_kb
    assert "Unified Fit" in retrieval_events[0].passages_by_kb["kb"][0]["text"]

    # The model actually saw the retrieved context this turn: it appears in
    # the message sent to the (mock) provider.
    sent_messages, _tools, _settings = provider.calls[0]
    assert any("Unified Fit models a SAXS curve" in m.content for m in sent_messages)

    await kb.embeddings_provider.aclose()
    kb.connection.close()


@pytest.mark.asyncio
async def test_send_never_persists_the_ephemeral_context_message_and_it_does_not_accumulate(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="answer 1"), MockTurn(text="answer 2")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    kb = await _seeded_active_kb(tmp_path)
    session = ChatSession(settings, "mock-profile", active_knowledge_bases=[kb])

    _ = [e async for e in session.send("How does Unified Fit work?")]
    after_first_turn = list(session.messages)
    assert not any("# Retrieved context for this question" in m.content for m in after_first_turn)

    _ = [e async for e in session.send("Tell me more about Unified Fit.")]
    after_second_turn = session.messages
    # Only ever the real user/assistant messages accumulate — the ephemeral
    # retrieval-context message from turn 1 never lingered into turn 2.
    assert not any("# Retrieved context for this question" in m.content for m in after_second_turn)
    assert len(after_second_turn) == len(after_first_turn) + 2  # +1 user, +1 assistant

    await kb.embeddings_provider.aclose()
    kb.connection.close()


@pytest.mark.asyncio
async def test_send_with_no_active_knowledge_bases_performs_no_retrieval(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="hi there")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    session = ChatSession(settings, "mock-profile")  # no active_knowledge_bases at all
    events = [e async for e in session.send("hello")]

    assert not any(isinstance(e, RetrievalPerformed) for e in events)


# --- context management (review findings 1 and 8) -------------------------
#
# trim_history existed but nothing ever called it, so self.messages grew for
# the whole session until the provider rejected a request for length,
# mid-analysis; and resumed history could arrive already broken (a crash
# mid-turn persists the assistant message announcing tool calls before their
# results exist), which every later turn's request is then rejected for.


@pytest.mark.asyncio
async def test_send_trims_the_history_to_the_configured_budget(
    monkeypatch, aida_home: Path, records_home: Path
):
    """PLAN.md §1.3: the real budget is now history_budget(context_window,
    reserved_output_tokens, tool_schema_tokens) — a tiny max_context_tokens
    like the old "200 (~800 characters)" setup gets clamped up to
    MIN_HISTORY_BUDGET (8000) rather than taken literally, since a budget
    that low is a misconfiguration, not an honest tight budget. So the
    fixture history here is sized to clearly exceed that floor regardless.
    Two scripted turns: compaction's own summarization call consumes the
    first (see ChatSession._compact_context), the actual reply consumes the
    second."""
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 200
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="summary of old turns"), MockTurn(text="ok")]),
    )

    session = ChatSession(settings, "mock-profile")
    for i in range(40):
        session.messages.append(Message(role="user", content=f"old question {i} " + "x" * 2000))
        session.messages.append(Message(role="assistant", content="old answer " + "y" * 2000))
    before = len(session.messages)

    async for _event in session.send("the new question"):
        pass

    assert len(session.messages) < before
    assert session.messages[-2].content == "the new question"
    await session.aclose()


@pytest.mark.asyncio
async def test_send_does_not_trim_when_the_budget_is_disabled(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 0
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))

    session = ChatSession(settings, "mock-profile")
    for i in range(40):
        session.messages.append(Message(role="user", content=f"old question {i} " + "x" * 400))
    before = len(session.messages)

    async for _event in session.send("the new question"):
        pass

    assert len(session.messages) == before + 2  # the new user message + the reply
    await session.aclose()


@pytest.mark.asyncio
async def test_send_yields_context_trimmed_event_when_it_actually_trims(
    monkeypatch, aida_home: Path, records_home: Path
):
    """B7: trimming used to be a log line only — send() now yields a
    ContextTrimmed event the frontend can show, with a real dropped_turns
    count and a post-trim token estimate. PLAN.md §1.3: with compaction
    wired in and a working summarization call scripted, the drop is
    reported as summarized rather than plain-dropped."""
    settings = _settings_with_profile()
    settings.app.max_context_tokens = 200
    monkeypatch.setattr(
        "aida.core.session.build_provider",
        lambda profile: MockProvider([MockTurn(text="summary of old turns"), MockTurn(text="ok")]),
    )

    session = ChatSession(settings, "mock-profile")
    for i in range(40):
        session.messages.append(Message(role="user", content=f"old question {i} " + "x" * 2000))
        session.messages.append(Message(role="assistant", content="old answer " + "y" * 2000))

    events = [e async for e in session.send("the new question")]
    trim_events = [e for e in events if isinstance(e, ContextTrimmed)]
    assert len(trim_events) == 1
    assert trim_events[0].dropped_turns > 0
    assert trim_events[0].estimated_tokens > 0
    assert trim_events[0].summarized is True
    assert trim_events[0].summary_tokens > 0
    await session.aclose()


@pytest.mark.asyncio
async def test_send_yields_no_context_trimmed_event_when_nothing_was_dropped(
    monkeypatch, aida_home: Path, records_home: Path
):
    settings = _settings_with_profile()  # default max_context_tokens is generous
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="ok")]))

    session = ChatSession(settings, "mock-profile")
    events = [e async for e in session.send("hello")]
    assert not any(isinstance(e, ContextTrimmed) for e in events)
    await session.aclose()


def test_estimate_message_tokens_counts_tool_call_arguments():
    """B7: estimate_tokens(message.content) alone ignored tool_calls
    entirely — a tool-heavy message (empty/short content, a big arguments
    payload) used to cost almost nothing in the trim budget."""
    from aida.core.context import estimate_message_tokens, estimate_tokens
    from aida.providers.base import ToolCall

    plain = Message(role="assistant", content="short reply")
    tool_heavy = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="c1", name="plot", arguments={"path": "x" * 2000})],
    )
    assert estimate_message_tokens(plain) == estimate_tokens("short reply")
    # A message with a large tool-call payload and empty content must cost
    # meaningfully more than one with only that empty content — before B7
    # it cost exactly estimate_tokens(""), regardless of arguments size.
    assert estimate_message_tokens(tool_heavy) > estimate_tokens("") + 400


def test_chat_session_repairs_broken_resumed_history(monkeypatch, aida_home: Path, records_home: Path):
    """A conversation killed mid-turn leaves an announced tool call with no
    result — a history the provider rejects outright on the next turn."""
    from aida.providers.base import ToolCall

    settings = _settings_with_profile()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: MockProvider([MockTurn(text="hi")]))
    broken = [
        Message(role="user", content="plot everything"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="plot", arguments={}), ToolCall(id="c2", name="plot", arguments={})],
        ),
        Message(role="tool", content="plotted", tool_call_id="c1", name="plot"),
    ]

    session = ChatSession(settings, "mock-profile", initial_messages=broken)

    announced = {tc.id for m in session.messages if m.role == "assistant" for tc in m.tool_calls}
    answered = {m.tool_call_id for m in session.messages if m.role == "tool"}
    assert announced == answered == {"c1", "c2"}


@pytest.mark.asyncio
async def test_ephemeral_context_message_is_removed_by_identity_not_equality(
    monkeypatch, aida_home: Path, records_home: Path, tmp_path: Path
):
    """send()'s finally removed the retrieval message with list.remove(),
    which matches by ``==`` — and ``Message`` is a plain dataclass, so an
    earlier message with identical field values is the one that gets
    deleted instead, silently losing real history. Persistence already
    excluded the context message by *identity*; removal now does too."""
    settings = _settings_with_profile()
    provider = MockProvider([MockTurn(text="answer")])
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: provider)

    kb = await _seeded_active_kb(tmp_path)
    session = ChatSession(settings, "mock-profile", active_knowledge_bases=[kb])

    # A real earlier message that happens to equal the context message this
    # turn will generate (the user pasted it back in, say).
    from aida.core.session import _format_retrieved_context

    passages = await session._retrieve_context("How does Unified Fit work?")
    look_alike = Message(role="user", content=_format_retrieved_context(passages))
    session.messages.append(look_alike)

    async for _event in session.send("How does Unified Fit work?"):
        pass

    assert any(m is look_alike for m in session.messages), "an equal-but-distinct message was removed"
    assert sum(1 for m in session.messages if m.content == look_alike.content) == 1

    await kb.embeddings_provider.aclose()
    kb.connection.close()
